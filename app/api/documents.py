"""Document endpoints. Every query is filtered by the caller's org_id (tenant isolation).
A document belonging to another org returns 404, not 403, so ids cannot be probed."""
import uuid

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_org_id, get_session, get_settings_dep, get_storage
from app.api.schemas import DocumentOut, FactOut, Page, StatusOut, TableOut
from app.config import PROCESSOR_VERSION, Settings
from app.documents import register_document
from app.errors import AppError
from app.jobs.queue import enqueue
from app.models import Document, DocumentVersion, ExtractedTable, FinancialFact, Job, ProcessingRun

router = APIRouter(prefix="/documents", tags=["documents"])


def _latest(session: Session, org_id: uuid.UUID, document_id: uuid.UUID) -> tuple[Document, DocumentVersion]:
    row = session.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.id == document_id, Document.org_id == org_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    ).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "Document not found")
    return row[0], row[1]


def _out(d: Document, v: DocumentVersion) -> DocumentOut:
    return DocumentOut(id=d.id, display_name=d.display_name, file_format=d.file_format, created_at=d.created_at,
                       version_id=v.id, status=v.status, sha256=v.sha256, size_bytes=v.size_bytes)


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Read in 1 MB pieces and stop as soon as the limit is exceeded (never buffer a 5 GB upload)."""
    chunks, total = [], 0
    while piece := await file.read(1024 * 1024):
        total += len(piece)
        if total > limit:
            raise AppError(413, "FILE_TOO_LARGE", f"File exceeds {limit} bytes")
        chunks.append(piece)
    if total == 0:
        raise AppError(400, "EMPTY_FILE", "File is empty")
    return b"".join(chunks)


@router.post("", status_code=202, response_model=DocumentOut)
async def upload_document(file: UploadFile, response: Response, org_id: uuid.UUID = Depends(get_org_id),
                          session: Session = Depends(get_session), storage=Depends(get_storage),
                          settings: Settings = Depends(get_settings_dep)) -> DocumentOut:
    """202 Accepted: stored and queued for processing. 200: identical file already exists."""
    data = await _read_limited(file, settings.max_upload_bytes)
    reg = register_document(session, storage, settings, org_id=org_id, filename=file.filename or "", data=data)
    if not reg.created:
        response.status_code = 200
    response.headers["Location"] = f"/documents/{reg.document_id}"
    return _out(*_latest(session, org_id, reg.document_id))


@router.get("", response_model=Page[DocumentOut])
def list_documents(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session)):
    total = session.scalar(select(func.count()).select_from(Document).where(Document.org_id == org_id))
    docs = session.scalars(select(Document).where(Document.org_id == org_id)
                           .order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)).all()
    return Page(items=[_out(*_latest(session, org_id, d.id)) for d in docs], total=total, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session)):
    return _out(*_latest(session, org_id, document_id))


@router.get("/{document_id}/status", response_model=StatusOut)
def get_status(document_id: uuid.UUID, org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session)):
    _, v = _latest(session, org_id, document_id)
    job = session.scalars(select(Job).where(Job.version_id == v.id).order_by(Job.created_at.desc()).limit(1)).first()
    run = session.get(ProcessingRun, v.active_run_id) if v.active_run_id else None
    return StatusOut(document_id=document_id, version_id=v.id, status=v.status, active_run_id=v.active_run_id,
                     job_status=job.status if job else None, attempts=job.attempts if job else None,
                     last_error_code=job.last_error_code if job else None, last_error=job.last_error if job else None,
                     run_stats=run.stats if run else None, run_warnings=run.warnings if run else None)


def _active_run(session: Session, org_id: uuid.UUID, document_id: uuid.UUID) -> uuid.UUID:
    _, v = _latest(session, org_id, document_id)
    if v.active_run_id is None:
        raise AppError(409, "NOT_PROCESSED", f"Document is not processed yet (status: {v.status})")
    return v.active_run_id


@router.get("/{document_id}/tables", response_model=Page[TableOut])
def get_tables(document_id: uuid.UUID, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
               org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session)):
    run_id = _active_run(session, org_id, document_id)
    base = select(ExtractedTable).where(ExtractedTable.run_id == run_id, ExtractedTable.org_id == org_id)
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    items = session.scalars(base.order_by(ExtractedTable.seq).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{document_id}/records", response_model=Page[FactOut])
def get_records(document_id: uuid.UUID, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                validation_status: str | None = None,
                org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session)):
    run_id = _active_run(session, org_id, document_id)
    base = select(FinancialFact).where(FinancialFact.run_id == run_id, FinancialFact.org_id == org_id)
    if validation_status:
        base = base.where(FinancialFact.validation_status == validation_status)
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    items = session.scalars(base.order_by(FinancialFact.row_key, FinancialFact.id).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/{document_id}/retry", status_code=202, response_model=StatusOut)
def retry(document_id: uuid.UUID, org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session),
          settings: Settings = Depends(get_settings_dep)):
    """Re-queue a document whose last job ended 'dead' (transient errors exhausted).
    Permanent failures (e.g. scanned PDF) are refused: retrying cannot help."""
    _, v = _latest(session, org_id, document_id)
    job = session.scalars(select(Job).where(Job.version_id == v.id).order_by(Job.created_at.desc()).limit(1)).first()
    if job is None or job.status != "dead":
        raise AppError(409, "NOT_RETRYABLE", f"Only 'dead' jobs can be retried (current: {job.status if job else 'none'})")
    enqueue(session, org_id=org_id, version_id=v.id, processor_version=PROCESSOR_VERSION,
            max_attempts=settings.job_max_attempts)
    v.status = "queued"
    session.commit()
    return get_status(document_id, org_id, session)
