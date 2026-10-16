"""Test fixtures. Tests run against a real Postgres database (ddq_test), not mocks:
constraints, SKIP LOCKED and full-text search are exactly what we need to verify."""
import os
import shutil
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import Organization
from app.security import hash_api_key
from app.storage import LocalStorage

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS = ROOT / "corpus"
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://ddq:ddq_local_only@localhost:5432/ddq_test")


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    if not (CORPUS / "northwind_growth_iii.pdf").exists() or not (FIXTURES / "scanned.pdf").exists():
        from scripts.make_corpus import main as make_corpus
        make_corpus()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.cmd_opts = type("o", (), {"x": [f"db_url={TEST_DB}"]})()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def engine():
    return make_engine(TEST_DB)


@pytest.fixture(autouse=True)
def _clean(engine):
    with engine.begin() as c:
        c.execute(text(
            "TRUNCATE organizations, documents, document_versions, processing_runs, jobs, text_chunks, "
            "extracted_tables, financial_facts, eval_runs, eval_results CASCADE"
        ))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=TEST_DB, storage_dir=str(tmp_path / "storage"), max_upload_bytes=2_000_000,
                    anthropic_api_key="")


@pytest.fixture
def factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def storage(settings):
    return LocalStorage(settings.storage_dir)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _make_org(factory, name: str) -> dict:
    key = f"test-key-{uuid.uuid4().hex}"
    with factory() as s:
        org = Organization(id=uuid.uuid4(), name=name, api_key_hash=hash_api_key(key))
        s.add(org)
        s.commit()
        return {"org_id": org.id, "headers": {"X-API-Key": key}}


@pytest.fixture
def org_a(factory):
    return _make_org(factory, "Org A")


@pytest.fixture
def org_b(factory):
    return _make_org(factory, "Org B")


def upload(client, headers, path: Path, name: str | None = None):
    with open(path, "rb") as fh:
        return client.post("/documents", headers=headers, files={"file": (name or path.name, fh)})


@pytest.fixture
def process(factory, storage):
    from app.worker import drain

    def _run():
        return drain(factory, storage, worker_id="test-worker")

    return _run
