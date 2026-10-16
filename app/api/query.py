"""Search and question endpoints."""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.answering.providers import GenerationError, get_provider
from app.answering.schemas import AnswerValidationError
from app.answering.service import AnswerResult, answer_question
from app.api.deps import get_org_id, get_session, get_settings_dep
from app.api.schemas import QuestionIn, SearchIn
from app.config import Settings
from app.errors import AppError
from app.retrieval import retrieve
from app.retrieval.common import Evidence

router = APIRouter(tags=["retrieval"])


@router.post("/search", response_model=list[Evidence])
def search(body: SearchIn, org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session)):
    return retrieve(session, org_id, body.query, strategy=body.strategy, chunk_size=body.chunk_size, k=body.top_k,
                    document_ids=body.document_ids)


@router.post("/questions", response_model=AnswerResult)
def ask(body: QuestionIn, org_id: uuid.UUID = Depends(get_org_id), session: Session = Depends(get_session),
        settings: Settings = Depends(get_settings_dep)):
    try:
        provider = get_provider(body.provider, settings.anthropic_api_key, settings.llm_model)
        return answer_question(session, org_id, body.query, strategy=body.strategy, chunk_size=body.chunk_size,
                               provider=provider, document_ids=body.document_ids, k=body.top_k)
    except GenerationError as e:
        status = 503 if e.code in ("LLM_UNAVAILABLE", "LLM_RATE_LIMITED", "LLM_NOT_CONFIGURED") else 502
        raise AppError(status, e.code, e.message) from e
    except AnswerValidationError as e:
        raise AppError(502, e.code, e.message) from e
