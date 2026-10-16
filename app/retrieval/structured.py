"""Approach B: retrieve whole table ROWS of typed facts, plus a little prose.

We rank individual facts with full-text search, then return every fact of the best rows,
so "management fee" brings back its rate AND basis AND frequency together, each value
labelled with its column header, table title, period and footnotes.
"""
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import FinancialFact
from app.processing.facts import render_row
from app.retrieval.common import Evidence, active_runs, to_tsquery_string
from app.retrieval.flat import search_chunks

TOP_ROWS_SQL = text(
    """
    SELECT f.run_id, f.table_id, f.row_key, max(ts_rank_cd(f.tsv, q)) AS score
    FROM financial_facts f, to_tsquery('english', :q) q
    WHERE f.org_id = :org AND f.run_id = ANY(:runs) AND f.tsv @@ q
    GROUP BY f.run_id, f.table_id, f.row_key
    ORDER BY score DESC, f.row_key
    LIMIT :k
    """
)


def search_rows(session: Session, org_id: uuid.UUID, question: str, *, k: int,
                document_ids: list[uuid.UUID] | None = None) -> list[Evidence]:
    q = to_tsquery_string(question)
    runs = active_runs(session, org_id, document_ids)
    if not q or not runs:
        return []
    top = session.execute(TOP_ROWS_SQL, {"q": q, "org": org_id, "runs": list(runs), "k": k}).all()
    out: list[Evidence] = []
    for r in top:
        facts = (
            session.query(FinancialFact)
            .filter(FinancialFact.org_id == org_id, FinancialFact.run_id == r.run_id, FinancialFact.row_key == r.row_key)
            .order_by(FinancialFact.location["col"].as_integer())
            .all()
        )
        loc = {k: v for k, v in facts[0].location.items() if k != "col"}
        loc["cells"] = [f.location.get("cell") or f"r{f.location['row']}c{f.location['col']}" for f in facts]
        out.append(
            Evidence(id=f"row:{r.table_id}:{r.row_key}", kind="fact_row", text=render_row(facts), score=float(r.score),
                     document_id=runs[r.run_id][0], document_name=runs[r.run_id][1], location=loc)
        )
    return out


def search_structured(session: Session, org_id: uuid.UUID, question: str, *, chunk_size: int, k: int,
                      document_ids: list[uuid.UUID] | None = None) -> list[Evidence]:
    """k total evidence items: up to k-2 table rows + 2 prose chunks (hybrid, fixed split)."""
    rows = search_rows(session, org_id, question, k=max(1, k - 2), document_ids=document_ids)
    prose = search_chunks(session, org_id, question, representation="prose", chunk_size=chunk_size,
                          k=k - len(rows), document_ids=document_ids)
    return rows + prose
