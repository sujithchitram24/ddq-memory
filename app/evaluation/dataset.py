"""Load and validate the evaluation set (eval/questions.yaml)."""
import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "eval" / "questions.yaml"


class Question(BaseModel):
    id: str
    fund: str
    category: str
    question: str
    expected_values: list[str] = Field(default_factory=list)
    evidence_labels: list[str] = Field(default_factory=list)
    should_abstain: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Question":
        if self.should_abstain == bool(self.expected_values):
            raise ValueError(f"{self.id}: exactly one of should_abstain / expected_values must be set")
        return self


class Dataset(BaseModel):
    funds: dict[str, str]
    questions: list[Question]
    sha256: str = ""

    @model_validator(mode="after")
    def _check(self) -> "Dataset":
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate question ids")
        unknown = {q.fund for q in self.questions} - set(self.funds)
        if unknown:
            raise ValueError(f"questions reference unknown funds {unknown}")
        return self


def load_dataset(path: Path = DEFAULT_PATH) -> Dataset:
    raw = path.read_bytes()
    ds = Dataset.model_validate(yaml.safe_load(raw))
    ds.sha256 = hashlib.sha256(raw).hexdigest()
    return ds
