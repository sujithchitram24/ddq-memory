"""Question answering: retrieve -> generate -> validate. No DB transaction is held during
generation (the retrieval session is finished before the LLM call)."""
import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.answering.providers import AnswerProvider
from app.answering.schemas import Answer, validate_against_evidence
from app.retrieval import Strategy, retrieve
from app.retrieval.common import Evidence


class AnswerResult(BaseModel):
    question: str
    strategy: str
    chunk_size: int
    provider: str
    answer: Answer
    evidence: list[Evidence]
    warnings: list[str]


def answer_question(session: Session, org_id: uuid.UUID, question: str, *, strategy: Strategy, chunk_size: int,
                    provider: AnswerProvider, document_ids: list[uuid.UUID] | None = None, k: int = 5) -> AnswerResult:
    evidence = retrieve(session, org_id, question, strategy=strategy, chunk_size=chunk_size, k=k,
                        document_ids=document_ids)
    session.rollback()  # end the read transaction before a potentially slow LLM call
    if not evidence:
        ans = Answer(answer="No relevant evidence was found.", abstained=True, abstain_reason="retrieval returned nothing")
        return AnswerResult(question=question, strategy=strategy, chunk_size=chunk_size, provider=provider.name,
                            answer=ans, evidence=[], warnings=[])
    ans = provider.generate(question, evidence)
    warnings = validate_against_evidence(ans, evidence)
    return AnswerResult(question=question, strategy=strategy, chunk_size=chunk_size, provider=provider.name,
                        answer=ans, evidence=evidence, warnings=warnings)
