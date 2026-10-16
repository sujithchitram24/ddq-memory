"""Worker process: claim a job, process it, record the outcome. Run: python -m app.worker

Failure handling:
- ExtractionError(retryable=False): permanent (e.g. scanned PDF) -> 'failed', no retry.
- Any other exception: assumed transient -> retried with backoff until max_attempts,
  then 'dead' for a human to look at.
- Worker crash (SIGKILL, OOM, node loss): nothing was committed for the run; the lease
  expires and another worker re-claims the job.
- SIGTERM (Kubernetes rolling update): finish the current job, then exit.
"""
import logging
import os
import signal
import socket
import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.extraction import errors as xerr
from app.extraction.errors import ExtractionError
from app.jobs import queue
from app.logging_setup import configure_logging, job_id_var
from app.processing.pipeline import LeaseLost, load_and_extract, persist
from app.storage import LocalStorage

logger = logging.getLogger("worker")


def process_one(factory: sessionmaker[Session], storage, worker_id: str, lease_seconds: int) -> str | None:
    """Claim and process at most one job. Returns the outcome, or None if the queue is empty."""
    with factory() as s:
        job = queue.claim(s, worker_id, lease_seconds)
    if job is None:
        return None
    token = job_id_var.set(str(job.id))
    try:
        if job.attempts > job.max_attempts:  # re-claimed after too many crashes
            with factory() as s:
                return queue.mark_failed(s, job, worker_id, code="TOO_MANY_ATTEMPTS",
                                         message="Lease expired too many times", retryable=False)
        try:
            with factory() as s:
                doc = load_and_extract(s, storage, job.version_id)
            with factory() as s:
                persist(s, job, worker_id, doc)
            logger.info("job succeeded")
            return "succeeded"
        except ExtractionError as e:
            vstatus = "needs_ocr" if e.code == xerr.NO_TEXT_LAYER else "failed"
            with factory() as s:
                outcome = queue.mark_failed(s, job, worker_id, code=e.code, message=e.message,
                                            retryable=e.retryable, version_status_on_terminal=vstatus)
            logger.warning("job failed: %s", e.code)
            return outcome
        except LeaseLost:
            logger.warning("lease lost; results discarded")
            return "lease_lost"
        except IntegrityError:
            # The run for this (version, processor_version) already exists: someone else
            # finished it. Treat as done, never as a duplicate.
            with factory() as s:
                queue.mark_succeeded(s, job, worker_id)
                s.commit()
            return "duplicate_ignored"
        except Exception as e:  # transient / unknown
            logger.exception("job error")
            with factory() as s:
                return queue.mark_failed(s, job, worker_id, code="UNEXPECTED", message=type(e).__name__, retryable=True)
    finally:
        job_id_var.reset(token)


def drain(factory: sessionmaker[Session], storage, worker_id: str = "inline", lease_seconds: int = 300) -> list[str]:
    """Process until no job is immediately available (used by tests and scripts)."""
    outcomes = []
    while (o := process_one(factory, storage, worker_id, lease_seconds)) is not None:
        outcomes.append(o)
    return outcomes


def main() -> None:
    configure_logging()
    settings = get_settings()
    factory = make_session_factory(make_engine(settings.database_url))
    storage = LocalStorage(settings.storage_dir)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True
        logger.info("shutdown requested; finishing current job")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("worker started", extra={"extra_fields": {"worker_id": worker_id}})
    while not stopping:
        if process_one(factory, storage, worker_id, settings.job_lease_seconds) is None:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
