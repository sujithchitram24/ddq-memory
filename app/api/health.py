"""Liveness: is the process alive? (never touches the DB - a DB blip must not restart every pod)
Readiness: can it serve traffic right now? (checks the DB - if not, take it out of the load balancer)"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        return JSONResponse({"status": "unavailable", "db": "error"}, status_code=503)
    return {"status": "ok", "db": "ok"}
