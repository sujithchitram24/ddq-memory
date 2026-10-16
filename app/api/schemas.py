"""Request/response models for the HTTP API (the public contract)."""
import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    display_name: str
    file_format: str
    created_at: datetime
    version_id: uuid.UUID
    status: str
    sha256: str
    size_bytes: int


class StatusOut(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    status: str
    active_run_id: uuid.UUID | None
    job_status: str | None
    attempts: int | None
    last_error_code: str | None
    last_error: str | None
    run_stats: dict[str, Any] | None
    run_warnings: list[Any] | None


class TableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    seq: int
    title: str | None
    header: list[str]
    rows: list[list[str | None]]
    footnotes: list[str]
    location: dict[str, Any]
    quality_flags: list[str]


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    table_id: uuid.UUID
    row_key: str
    table_title: str | None
    row_label: str
    column_label: str
    value_raw: str
    value_numeric: float | None
    value_kind: str
    unit: str | None
    currency: str | None
    period: str | None
    footnotes: list[str]
    location: dict[str, Any]
    extraction_method: str
    processor_version: str
    validation_status: str


class SearchIn(BaseModel):
    # The /docs page pre-fills request bodies from this example. Without it, Swagger
    # invents a random document id, the search finds nothing, and every strategy
    # returns the same "no evidence" answer.
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"query": "What is the management fee and what is it calculated on?", "strategy": "structured", "chunk_size": 50}
    ]})

    query: str = Field(min_length=2, max_length=500)
    strategy: Literal["flat", "structured"] = "structured"
    chunk_size: Literal[50, 200] = 200
    top_k: int = Field(default=5, ge=1, le=20)
    document_ids: list[uuid.UUID] | None = Field(
        default=None, description="Optional: limit to these documents. Leave out to search all your documents.")


class QuestionIn(SearchIn):
    provider: Literal["deterministic", "anthropic"] = "deterministic"


class EvaluationIn(BaseModel):
    strategies: list[Literal["flat", "structured"]] = ["flat", "structured"]
    chunk_sizes: list[Literal[50, 200]] = [50, 200]
    provider: Literal["deterministic", "anthropic"] = "deterministic"
