"""Document registration: validate -> store bytes -> insert document + version + job in ONE transaction."""
import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import PROCESSOR_VERSION, Settings
from app.jobs.queue import enqueue
from app.models import Document, DocumentVersion
from app.security import detect_format, safe_display_name
from app.storage import Storage


@dataclass
class Registered:
    document_id: uuid.UUID
    version_id: uuid.UUID
    created: bool  # False = identical bytes already uploaded (idempotent)


def register_document(session: Session, storage: Storage, settings: Settings, *, org_id: uuid.UUID,
                      filename: str, data: bytes) -> Registered:
    file_format = detect_format(filename, data, settings.max_unzipped_bytes)
    sha = hashlib.sha256(data).hexdigest()

    existing = session.execute(
        select(DocumentVersion).where(DocumentVersion.org_id == org_id, DocumentVersion.sha256 == sha)
    ).scalar_one_or_none()
    if existing:
        return Registered(existing.document_id, existing.id, created=False)

    key = f"{org_id}/{sha}.{file_format}"
    storage.put(key, data)  # content-addressed: writing the same bytes twice is harmless
    doc = Document(id=uuid.uuid4(), org_id=org_id, display_name=safe_display_name(filename), file_format=file_format)
    ver = DocumentVersion(id=uuid.uuid4(), org_id=org_id, document_id=doc.id, version_number=1, sha256=sha,
                          storage_key=key, size_bytes=len(data), status="queued")
    session.add(doc)
    session.flush()  # documents row must exist before the version references it
    session.add(ver)
    try:
        session.flush()
        enqueue(session, org_id=org_id, version_id=ver.id, processor_version=PROCESSOR_VERSION,
                max_attempts=settings.job_max_attempts)
        session.commit()
    except IntegrityError:
        # Lost a race with a concurrent identical upload: the unique constraint on
        # (org_id, sha256) rejected us. Return the winner's ids.
        session.rollback()
        winner = session.execute(
            select(DocumentVersion).where(DocumentVersion.org_id == org_id, DocumentVersion.sha256 == sha)
        ).scalar_one()
        return Registered(winner.document_id, winner.id, created=False)
    return Registered(doc.id, ver.id, created=True)
