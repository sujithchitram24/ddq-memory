"""Retrieval over text chunks with Postgres full-text search (GIN index on a tsvector)."""
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.retrieval.common import Evidence, active_runs, to_tsquery_string

CHUNK_SQL = text(
    """
    SELECT c.id, c.run_id, c.content, c.location, ts_rank_cd(c.tsv, q) AS score
    FROM text_chunks c, to_tsquery('english', :q) q
    WHERE c.org_id = :org AND c.run_id = ANY(:runs)
      AND c.representation = :repr AND c.chunk_size = :size
      AND c.tsv @@ q
    ORDER BY score DESC, c.seq
    LIMIT :k
    """
)


def search_chunks(session: Session, org_id: uuid.UUID, question: str, *, representation: str,
                  chunk_size: int, k: int, document_ids: list[uuid.UUID] | None = None) -> list[Evidence]:
    q = to_tsquery_string(question)
    runs = active_runs(session, org_id, document_ids)
    if not q or not runs:
        return []
    rows = session.execute(CHUNK_SQL, {"q": q, "org": org_id, "runs": list(runs), "repr": representation,
                                       "size": chunk_size, "k": k}).all()
    return [
        Evidence(id=f"chunk:{r.id}", kind="chunk", text=r.content, score=float(r.score),
                 document_id=runs[r.run_id][0], document_name=runs[r.run_id][1], location=r.location)
        for r in rows
    ]
