"""The answer contract. Every answer is validated before it is returned.

Valid JSON is not the same as a correct answer. Checks here are structural (shape,
citations must reference evidence we actually supplied, numbers in claims must appear
in the cited evidence). They catch a class of hallucinations, not all of them.
"""
import re

from pydantic import BaseModel, Field, model_validator

from app.retrieval.common import Evidence


class Claim(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class Answer(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Answer":
        if self.abstained and self.claims:
            raise ValueError("an abstention must not contain claims")
        if not self.abstained and not self.claims:
            raise ValueError("a non-abstaining answer must contain at least one cited claim")
        return self


class AnswerValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(s)}


def validate_against_evidence(answer: Answer, evidence: list[Evidence]) -> list[str]:
    """Raise on fabricated citations; return warnings for numbers not found in evidence."""
    by_id = {e.id: e for e in evidence}
    warnings: list[str] = []
    for claim in answer.claims:
        unknown = [i for i in claim.evidence_ids if i not in by_id]
        if unknown:
            raise AnswerValidationError("CITATION_NOT_IN_EVIDENCE", f"claim cites unknown evidence {unknown}")
        cited = " ".join(by_id[i].text for i in claim.evidence_ids)
        missing = _numbers(claim.text) - _numbers(cited)
        if missing:
            warnings.append(f"unsupported numbers {sorted(missing)} in claim")
    return warnings
