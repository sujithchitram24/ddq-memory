"""Evaluation endpoints. POST runs synchronously: ~140 answers with the deterministic
provider take a few seconds. With a real LLM this would become a queued job."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answering.providers import GenerationError, get_provider
from app.api.deps import get_org_id, get_session, get_settings_dep
from app.api.schemas import EvaluationIn
from app.config import Settings
from app.errors import AppError
from app.evaluation.dataset import load_dataset
from app.evaluation.runner import MissingCorpus, run_evaluation
from app.models import EvalResult, EvalRun

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


class EvaluationOut(BaseModel):
    id: uuid.UUID
    dataset_sha256: str
    config: dict
    summary: dict
    results: list[dict] | None = None


@router.post("", status_code=201, response_model=EvaluationOut)
def create(body: EvaluationIn, org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session),
           settings: Settings = Depends(get_settings_dep)):
    try:
        provider = get_provider(body.provider, settings.anthropic_api_key, settings.llm_model)
        run = run_evaluation(session, org_id, load_dataset(), provider, body.strategies, body.chunk_sizes)
    except MissingCorpus as e:
        raise AppError(409, "CORPUS_NOT_READY", str(e)) from e
    except GenerationError as e:
        raise AppError(503, e.code, e.message) from e
    return EvaluationOut(id=run.id, dataset_sha256=run.dataset_sha256, config=run.config, summary=run.summary)


@router.get("/{evaluation_id}", response_model=EvaluationOut)
def get(evaluation_id: uuid.UUID, include_results: bool = False, org_id: uuid.UUID = Depends(get_org_id),
        session: Session = Depends(get_session)):
    run = session.scalars(select(EvalRun).where(EvalRun.id == evaluation_id, EvalRun.org_id == org_id)).first()
    if run is None:
        raise AppError(404, "NOT_FOUND", "Evaluation not found")
    results = None
    if include_results:
        results = [
            {"question_id": r.question_id, "strategy": r.strategy, "chunk_size": r.chunk_size, "scores": r.scores,
             "answer": r.answer.get("answer", r.answer)}
            for r in session.scalars(select(EvalResult).where(EvalResult.eval_run_id == run.id)
                                     .order_by(EvalResult.question_id, EvalResult.strategy, EvalResult.chunk_size))
        ]
    return EvaluationOut(id=run.id, dataset_sha256=run.dataset_sha256, config=run.config, summary=run.summary,
                         results=results)
