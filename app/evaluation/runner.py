"""Run every question through every (strategy, chunk_size) configuration and store results.

Everything except the configuration is held fixed: corpus, questions, provider, k, and
the answer algorithm. The deterministic provider makes runs exactly repeatable.
"""
import uuid
from itertools import product

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answering.providers import AnswerProvider, GenerationError
from app.answering.schemas import AnswerValidationError
from app.answering.service import answer_question
from app.evaluation.dataset import Dataset
from app.evaluation.scoring import score, summarise
from app.models import Document, EvalResult, EvalRun

TOP_K = 5


class MissingCorpus(Exception):
    pass


def fund_documents(session: Session, org_id: uuid.UUID, ds: Dataset) -> dict[str, uuid.UUID]:
    docs = {d.display_name: d.id for d in session.scalars(select(Document).where(Document.org_id == org_id))}
    missing = [f for f in ds.funds.values() if f not in docs]
    if missing:
        raise MissingCorpus(f"corpus documents not uploaded/processed in this org: {missing}")
    return {fund: docs[name] for fund, name in ds.funds.items()}


def run_evaluation(session: Session, org_id: uuid.UUID, ds: Dataset, provider: AnswerProvider,
                   strategies: list[str], chunk_sizes: list[int]) -> EvalRun:
    fund_docs = fund_documents(session, org_id, ds)
    run = EvalRun(id=uuid.uuid4(), org_id=org_id, dataset_sha256=ds.sha256,
                  config={"strategies": strategies, "chunk_sizes": chunk_sizes, "provider": provider.name, "top_k": TOP_K})
    session.add(run)
    session.commit()

    summary: dict[str, dict] = {}
    # Collected in memory and written at the end: answer_question() ends its read
    # transaction (rollback) before generation, which would discard pending inserts.
    pending: list[EvalResult] = []
    for strategy, size in product(strategies, chunk_sizes):
        rows = []
        for q in ds.questions:
            error = None
            try:
                res = answer_question(session, org_id, q.question, strategy=strategy, chunk_size=size,
                                      provider=provider, document_ids=[fund_docs[q.fund]], k=TOP_K)
                s = score(q, res)
                answer_json = res.model_dump(mode="json")
            except (GenerationError, AnswerValidationError) as e:
                error, s, answer_json = e.code, None, {"error": e.code, "message": e.message}
            rows.append({"question": q, "scores": s, "error": error})
            pending.append(EvalResult(eval_run_id=run.id, question_id=q.id, strategy=strategy, chunk_size=size,
                                   answer=answer_json, scores=s or {"error": error}))
        summary[f"{strategy}/{size}"] = summarise(rows)
    run = session.get(EvalRun, run.id)
    run.summary = summary
    session.add_all(pending)
    session.commit()
    return run
