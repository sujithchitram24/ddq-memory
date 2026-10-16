"""FastAPI application factory. Run: uvicorn app.main:create_app --factory --reload"""
import logging
import time
import uuid

from fastapi import FastAPI, Request

from app.api import documents, evaluations, health, query
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.errors import install_error_handlers
from app.logging_setup import configure_logging, request_id_var
from app.storage import LocalStorage

logger = logging.getLogger("api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    app = FastAPI(title="Structured DDQ Memory", version="0.1.0",
                  description="Flattened vs structured table retrieval for DDQ documents (synthetic data).")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.storage = LocalStorage(settings.storage_dir)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(rid[:64])
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid[:64]
        logger.info("request", extra={"extra_fields": {"method": request.method, "path": request.url.path,
                                                       "status": response.status_code, "request_id": rid[:64],
                                                       "ms": round((time.perf_counter() - start) * 1000, 1)}})
        return response

    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(query.router)
    app.include_router(evaluations.router)
    return app

