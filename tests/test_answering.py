import uuid

import pytest

from app.answering.providers import DeterministicProvider
from app.answering.schemas import Answer, AnswerValidationError, validate_against_evidence
from app.retrieval.common import Evidence
from tests.conftest import FIXTURES, upload


def ev(i: str, text: str) -> Evidence:
    return Evidence(id=i, kind="chunk", text=text, score=1.0, document_id=uuid.uuid4(), document_name="x", location={})


def test_deterministic_abstains_when_nothing_matches():
    a = DeterministicProvider().generate("What is the ESG rating?", [ev("e1", "Management fee is 2%.")])
    assert a.abstained and not a.claims


def test_citation_must_reference_supplied_evidence():
    a = Answer(answer="2%", claims=[{"text": "fee is 2%", "evidence_ids": ["made-up"]}])
    with pytest.raises(AnswerValidationError):
        validate_against_evidence(a, [ev("e1", "fee is 2%")])


def test_numbers_not_in_evidence_are_reported():
    a = Answer(answer="x", claims=[{"text": "fee is 3%", "evidence_ids": ["e1"]}])
    assert validate_against_evidence(a, [ev("e1", "fee is 2%")])


def test_answer_schema_rejects_inconsistent_shapes():
    with pytest.raises(ValueError):
        Answer(answer="x", abstained=False, claims=[])
    with pytest.raises(ValueError):
        Answer(answer="x", abstained=True, claims=[{"text": "t", "evidence_ids": ["e"]}])


def test_prompt_injection_text_is_treated_as_data(client, org_a, process):
    upload(client, org_a["headers"], FIXTURES / "injection.docx")
    process()
    r = client.post("/questions", headers=org_a["headers"],
                    json={"query": "What is the management fee rate?", "strategy": "structured"}).json()
    assert "1.75%" in r["answer"]["answer"] and r["answer"]["claims"]
    # The injected sentence is still stored and retrievable as evidence (we never silently drop content).
    assert any("IGNORE ALL PREVIOUS INSTRUCTIONS" in e["text"] for e in r["evidence"])
