"""Database schema (SQLAlchemy 2.x declarative models).

Design rules (see docs/DATABASE.md for the full reasoning):
- Every tenant-owned row carries org_id, and every query filters on it.
- Things we query, join, or constrain are relational columns.
- Things we only store and return as a whole (a table's raw grid, a location, quality
  flags) are JSONB.
- Everything a processing run produces points at that run (run_id), so reprocessing
  never overwrites history; document_versions.active_run_id says which run is current.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


# --------------------------------------------------------------------------- tenants


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # We store a SHA-256 hash of the API key, never the key itself.
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at()


# ------------------------------------------------------------------------- documents


class Document(Base):
    """A logical document (e.g. 'Northwind Fund III DDQ response'). Has 1..n versions."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        CheckConstraint("file_format IN ('pdf','docx','xlsx')", name="ck_documents_format"),
        Index("ix_documents_org_created", "org_id", "created_at"),
    )


class DocumentVersion(Base):
    """One exact set of bytes. Identified by its SHA-256 hash within an organisation."""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # queued -> processing -> succeeded | failed | needs_ocr
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # Which processing run's output is "current". NULL until the first run succeeds.
    # Plain UUID, not an FK, to avoid a circular FK with processing_runs; integrity is
    # enforced by writing both in the same transaction.
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        # Idempotent upload: same bytes in the same org = same version.
        UniqueConstraint("org_id", "sha256", name="uq_versions_org_sha256"),
        UniqueConstraint("document_id", "version_number", name="uq_versions_doc_number"),
        CheckConstraint(
            "status IN ('queued','processing','succeeded','failed','needs_ocr')",
            name="ck_versions_status",
        ),
    )


class ProcessingRun(Base):
    """One successful execution of the extraction pipeline over one document version.

    Rows are only ever written inside the same transaction as all of the run's output,
    so a run row existing means its output is complete.
    """

    __tablename__ = "processing_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    processor_version: Mapped[str] = mapped_column(String(40), nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        # Second line of defence against duplicate work: one run per version per
        # processor version. A duplicate job that slips through fails on insert.
        UniqueConstraint("version_id", "processor_version", name="uq_runs_version_processor"),
    )


# ------------------------------------------------------------------------------ jobs


class Job(Base):
    """Durable work queue stored in Postgres. Workers claim with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    processor_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # queued -> running -> succeeded | queued (retry) | failed (permanent) | dead (retries exhausted)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','dead')", name="ck_jobs_status"
        ),
        # At most one *active* job per (version, processor_version, kind).
        Index(
            "uq_jobs_active",
            "version_id",
            "processor_version",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        # The claim query scans this.
        Index("ix_jobs_claim", "status", "run_after"),
    )


# ------------------------------------------------------------------ extracted content


class TextChunk(Base):
    """Baseline A ("flat") and prose for Approach B.

    representation:
      'flat'  - whole document linearised (tables rendered as markdown) then chunked
      'prose' - only non-table text, chunked (used alongside structured facts)
    """

    __tablename__ = "text_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False
    )
    representation: Mapped[str] = mapped_column(String(10), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)  # target words
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )

    __table_args__ = (
        CheckConstraint("representation IN ('flat','prose')", name="ck_chunks_repr"),
        UniqueConstraint("run_id", "representation", "chunk_size", "seq", name="uq_chunks_seq"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_run_repr_size", "run_id", "representation", "chunk_size"),
    )


class ExtractedTable(Base):
    """A table exactly as extracted, kept for fidelity, debugging and re-normalisation.

    The grid is JSONB: we always read/write it whole and never query single cells by
    SQL. The queryable form of a table is financial_facts.
    """

    __tablename__ = "extracted_tables"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    header: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rows: Mapped[list[list[str | None]]] = mapped_column(JSONB, nullable=False)
    footnotes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_tables_run_seq"),)


class FinancialFact(Base):
    """One typed cell: (row label, column label) -> value, with full provenance.

    Cells from the same table row share row_key, so retrieval can return a whole row
    (e.g. rate AND basis AND frequency of the management fee) together.
    """

    __tablename__ = "financial_facts"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extracted_tables.id", ondelete="CASCADE"), nullable=False
    )
    row_key: Mapped[str] = mapped_column(String(80), nullable=False)
    table_title: Mapped[str | None] = mapped_column(Text)
    row_label: Mapped[str] = mapped_column(Text, nullable=False)
    column_label: Mapped[str] = mapped_column(Text, nullable=False)
    value_raw: Mapped[str] = mapped_column(Text, nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    value_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str | None] = mapped_column(String(3))
    period: Mapped[str | None] = mapped_column(String(40))
    footnotes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(40), nullable=False)
    processor_version: Mapped[str] = mapped_column(String(40), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(12), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', search_text)", persisted=True)
    )

    __table_args__ = (
        CheckConstraint(
            "value_kind IN ('percent','money','number','year','text','empty')",
            name="ck_facts_kind",
        ),
        CheckConstraint(
            "validation_status IN ('parsed','unparsed','missing')", name="ck_facts_validation"
        ),
        Index("ix_facts_tsv", "tsv", postgresql_using="gin"),
        Index("ix_facts_run_row", "run_id", "row_key"),
        Index("ix_facts_table", "table_id"),
    )


# ------------------------------------------------------------------------ evaluation


class EvalRun(Base):
    """One evaluation over the question set. Questions live in eval/questions.yaml (in
    git, reviewed like code); we store the dataset hash so results are traceable."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "eval_run_id", "question_id", "strategy", "chunk_size", name="uq_eval_results_cell"
        ),
    )
