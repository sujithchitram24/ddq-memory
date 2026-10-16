"""Structured JSON logs with request and job correlation IDs.

Every log line carries request_id (HTTP) or job_id (worker), so one upload can be traced
from the API call to the worker that processed it. Never log document contents or keys.
"""
import json
import logging
import re
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

SECRET_PATTERNS = [
    re.compile(r"(postgresql(\+psycopg)?://[^:]+:)[^@]+@"),  # DB URL passwords
    re.compile(r"(sk-ant-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+"),  # Anthropic-style keys
]


def redact(text: str) -> str:
    for p in SECRET_PATTERNS:
        text = p.sub(r"\1***", text) if p.groups else p.sub("***", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": request_id_var.get(),
            "job_id": job_id_var.get(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps({k: v for k, v in payload.items() if v is not None}, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, msg: str, **fields: object) -> None:
    logger.info(msg, extra={"extra_fields": fields})
