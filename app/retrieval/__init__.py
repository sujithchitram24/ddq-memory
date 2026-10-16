"""One entry point so the API and the evaluation call retrieval identically."""
import uuid
from typing import Literal

from sqlalchemy.orm import Session

from app.retrieval.common import Evidence
from app.retrieval.flat import search_chunks
from app.retrieval.structured import search_structured

Strategy = Literal["flat", "structured"]


def retrieve(session: Session, org_id: uuid.UUID, question: str, *, strategy: Strategy, chunk_size: int,
             k: int = 5, document_ids: list[uuid.UUID] | None = None) -> list[Evidence]:
    if strategy == "flat":
        return search_chunks(session, org_id, question, representation="flat", chunk_size=chunk_size, k=k,
                             document_ids=document_ids)
    return search_structured(session, org_id, question, chunk_size=chunk_size, k=k, document_ids=document_ids)
