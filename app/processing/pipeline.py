"""Document processing: extract (no DB transaction) -> persist everything (one transaction).

Why two phases: extraction is slow (and in a real system may call an LLM). Holding a
database transaction open during slow work pins a connection and holds locks. So we do
the slow part first, then write all results atomically in one short transaction that
also completes the job. Either everything from a run is visible, or nothing is.
"""
import logging
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.extraction import extract
from app.extraction.ir import ExtractedDocument
from app.jobs.queue import ClaimedJob, mark_succeeded
from app.models import DocumentVersion, ExtractedTable, FinancialFact, ProcessingRun, TextChunk, Document
from app.processing.chunking import CHUNK_SIZES, chunk, linearize
from app.processing.facts import table_to_facts

logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    """Another worker took over this job; our results must not be committed."""


def load_and_extract(session: Session, storage, version_id: uuid.UUID) -> ExtractedDocument:
    version = session.get(DocumentVersion, version_id)
    document = session.get(Document, version.document_id)
    session.rollback()  # release the read snapshot before slow work
    data = storage.get(version.storage_key)
    return extract(document.file_format, data)


def persist(session: Session, job: ClaimedJob, worker_id: str, doc: ExtractedDocument) -> uuid.UUID:
    """Write one run's full output in ONE transaction. Raises LeaseLost (after rollback)."""
    run = ProcessingRun(
        id=uuid.uuid4(),
        org_id=job.org_id,
        version_id=job.version_id,
        job_id=job.id,
        processor_version=job.processor_version,
        warnings=doc.warnings,
    )
    session.add(run)
    session.flush()  # surfaces the unique (version, processor_version) constraint early

    n_chunks = 0
    for representation, include_tables in (("flat", True), ("prose", False)):
        units = linearize(doc, include_tables=include_tables)
        for size in CHUNK_SIZES:
            for seq, (content, loc) in enumerate(chunk(units, size)):
                session.add(TextChunk(org_id=job.org_id, run_id=run.id, representation=representation,
                                      chunk_size=size, seq=seq, content=content, location=loc))
                n_chunks += 1

    n_facts = 0
    for seq, table in enumerate(doc.tables):
        t = ExtractedTable(id=uuid.uuid4(), org_id=job.org_id, run_id=run.id, seq=seq, title=table.title,
                           header=table.header, rows=table.rows, footnotes=table.footnotes,
                           location=table.location, quality_flags=table.quality_flags)
        session.add(t)
        for f in table_to_facts(table, seq, doc.parser):
            session.add(FinancialFact(org_id=job.org_id, run_id=run.id, table_id=t.id,
                                      processor_version=job.processor_version, **f.__dict__))
            n_facts += 1

    run.stats = {"blocks": len(doc.blocks), "tables": len(doc.tables), "chunks": n_chunks, "facts": n_facts,
                 "flagged_tables": sum(1 for t in doc.tables if t.quality_flags)}
    session.execute(
        update(DocumentVersion).where(DocumentVersion.id == job.version_id)
        .values(active_run_id=run.id, status="succeeded")
    )
    if not mark_succeeded(session, job, worker_id):
        session.rollback()
        raise LeaseLost(str(job.id))
    session.commit()
    logger.info("run committed", extra={"extra_fields": {"run_id": str(run.id), **run.stats}})
    return run.id
