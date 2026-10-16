"""FastAPI dependencies: DB session, storage, settings, and the authenticated organisation."""
import uuid
from collections.abc import Iterator

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.models import Organization
from app.security import hash_api_key


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_storage(request: Request):
    return request.app.state.storage


def get_org_id(x_api_key: str | None = Header(default=None), session: Session = Depends(get_session)) -> uuid.UUID:
    if not x_api_key:
        raise AppError(401, "UNAUTHENTICATED", "Missing X-API-Key header")
    org_id = session.execute(
        select(Organization.id).where(Organization.api_key_hash == hash_api_key(x_api_key))
    ).scalar_one_or_none()
    if org_id is None:
        raise AppError(401, "UNAUTHENTICATED", "Invalid API key")
    return org_id
