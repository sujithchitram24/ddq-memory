"""Database engine and session factory (SQLAlchemy 2.x, synchronous, psycopg3 driver).

Why sync: the expensive work here is CPU-bound parsing, and the worker is a plain loop.
FastAPI runs `def` endpoints in a threadpool, so sync is simpler and fast enough.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_size=5,  # connections kept open per process
        max_overflow=5,  # extra connections allowed under burst
        pool_pre_ping=True,  # detect dead connections (e.g. after a DB failover)
        connect_args={"connect_timeout": 2},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """FastAPI dependency body: one session per request, always closed."""
    session = factory()
    try:
        yield session
    finally:
        session.close()
