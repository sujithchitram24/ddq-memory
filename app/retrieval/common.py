"""Shared retrieval pieces: evidence shape, query building, scoping to active runs."""
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

STOPWORDS = set(
    "a an and are as at be by does do for from how in is it its of on or the this that to "
    "was what when where which who why with fund funds".split()
)


class Evidence(BaseModel):
    id: str  # stable reference the answer must cite, e.g. "chunk:<uuid>" or "row:<uuid>:2:0"
    kind: Literal["chunk", "fact_row"]
    text: str
    score: float
    document_id: uuid.UUID
    document_name: str
    location: dict[str, Any]


def to_tsquery_string(question: str) -> str | None:
    """OR-query of the question's content words. Only [a-z0-9] survive, and the string is
    passed as a bind parameter, so this cannot inject SQL or tsquery operators."""
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in STOPWORDS]
    return " | ".join(dict.fromkeys(words)) or None


ACTIVE_RUNS_SQL = text(
    """
    SELECT DISTINCT ON (dv.document_id) dv.active_run_id AS run_id, d.id AS document_id, d.display_name
    FROM document_versions dv JOIN documents d ON d.id = dv.document_id
    WHERE dv.org_id = :org AND dv.active_run_id IS NOT NULL
      AND (CAST(:docs AS uuid[]) IS NULL OR dv.document_id = ANY(CAST(:docs AS uuid[])))
    ORDER BY dv.document_id, dv.version_number DESC
    """
)


def active_runs(session: Session, org_id: uuid.UUID, document_ids: list[uuid.UUID] | None) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """run_id -> (document_id, display_name) for the latest processed version of each document."""
    rows = session.execute(ACTIVE_RUNS_SQL, {"org": org_id, "docs": document_ids}).all()
    return {r.run_id: (r.document_id, r.display_name) for r in rows}
