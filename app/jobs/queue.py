"""A durable job queue in Postgres.

Why Postgres and not SQS/Redis/Celery: we already run Postgres, jobs are few and heavy,
and enqueueing in the SAME transaction as the document insert means we can never have
a document without a job or a job without a document. Move to SQS when job volume or
fan-out outgrows one database, or when non-Postgres consumers need the events.

Key ideas:
- claim(): SELECT ... FOR UPDATE SKIP LOCKED lets many workers pull different jobs
  concurrently without blocking each other or taking the same job.
- leases: a claimed job has lease_until. If the worker dies, the lease expires and
  another worker re-claims it. No manual cleanup needed.
- fencing: completion only succeeds if this worker still holds the lease (locked_by),
  so a slow "zombie" worker cannot overwrite a newer attempt.
"""
import random
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

CLAIM_SQL = text(
    """
    WITH next AS (
        SELECT id FROM jobs
        WHERE (status = 'queued' AND run_after <= now())
           OR (status = 'running' AND lease_until < now())   -- crashed worker
        ORDER BY run_after
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE jobs SET status = 'running',
                    attempts = jobs.attempts + 1,
                    locked_by = :worker,
                    lease_until = now() + make_interval(secs => :lease),
                    updated_at = now()
    FROM next WHERE jobs.id = next.id
    RETURNING jobs.id, jobs.org_id, jobs.version_id, jobs.attempts, jobs.max_attempts,
              jobs.processor_version, jobs.kind
    """
)


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    org_id: uuid.UUID
    version_id: uuid.UUID
    attempts: int
    max_attempts: int
    processor_version: str
    kind: str


def enqueue(session: Session, *, org_id: uuid.UUID, version_id: uuid.UUID, processor_version: str, max_attempts: int) -> uuid.UUID:
    """Caller owns the transaction. ON CONFLICT makes enqueue idempotent: if an active
    job already exists for this version + processor version, nothing is added."""
    row = session.execute(
        text(
            """
            INSERT INTO jobs (id, org_id, kind, version_id, processor_version, status, attempts, max_attempts, run_after)
            VALUES (:id, :org, 'process_document', :version, :pv, 'queued', 0, :max, now())
            ON CONFLICT (version_id, processor_version, kind) WHERE status IN ('queued','running')
            DO NOTHING
            RETURNING id
            """
        ),
        {"id": uuid.uuid4(), "org": org_id, "version": version_id, "pv": processor_version, "max": max_attempts},
    ).first()
    if row:
        return row.id
    return session.execute(
        text("SELECT id FROM jobs WHERE version_id=:v AND processor_version=:pv AND status IN ('queued','running')"),
        {"v": version_id, "pv": processor_version},
    ).scalar_one()


def claim(session: Session, worker_id: str, lease_seconds: int) -> ClaimedJob | None:
    """Short transaction: claim one job and commit immediately."""
    row = session.execute(CLAIM_SQL, {"worker": worker_id, "lease": lease_seconds}).first()
    if row is None:
        session.commit()
        return None
    session.execute(
        text("UPDATE document_versions SET status='processing' WHERE id=:v AND status <> 'succeeded'"),
        {"v": row.version_id},
    )
    session.commit()
    return ClaimedJob(**row._mapping)


def mark_succeeded(session: Session, job: ClaimedJob, worker_id: str) -> bool:
    """Fenced completion. Returns False if we lost the lease; caller must roll back."""
    res = session.execute(
        text(
            "UPDATE jobs SET status='succeeded', lease_until=NULL, updated_at=now(), last_error=NULL, last_error_code=NULL "
            "WHERE id=:id AND locked_by=:w AND status='running'"
        ),
        {"id": job.id, "w": worker_id},
    )
    return res.rowcount == 1


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: ~2s, 4s, 8s ... capped at 5 min."""
    return min(300.0, 2.0**attempt) * random.uniform(0.75, 1.25)


def mark_failed(
    session: Session,
    job: ClaimedJob,
    worker_id: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    version_status_on_terminal: str = "failed",
) -> str:
    """Decide retry vs terminal. Returns the job's new status."""
    if retryable and job.attempts < job.max_attempts:
        new_status = "queued"
        run_after_sql = "now() + make_interval(secs => :delay)"
    else:
        new_status = "dead" if retryable else "failed"
        run_after_sql = "run_after"
    res = session.execute(
        text(
            f"UPDATE jobs SET status=:s, run_after={run_after_sql}, lease_until=NULL, locked_by=NULL, "
            "last_error_code=:code, last_error=:msg, updated_at=now() "
            "WHERE id=:id AND locked_by=:w AND status='running'"
        ),
        {"s": new_status, "delay": backoff_seconds(job.attempts), "code": code, "msg": message[:2000], "id": job.id, "w": worker_id},
    )
    if res.rowcount == 1 and new_status in ("failed", "dead"):
        session.execute(
            text("UPDATE document_versions SET status=:vs WHERE id=:v AND status <> 'succeeded'"),
            {"vs": version_status_on_terminal, "v": job.version_id},
        )
    session.commit()
    return new_status


def now(session: Session) -> datetime:
    return session.execute(text("SELECT now()")).scalar_one()
