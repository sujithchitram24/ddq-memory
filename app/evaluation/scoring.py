"""Scoring rules. Each is a plain function so it can be unit-tested on hand-made answers.

Metrics (per question):
- correct          answerable: not abstained AND every expected value in the answer text.
                   should_abstain: abstained.
- association      one CITED evidence item contains every expected value AND every
                   evidence label (value next to its row label / column header / period).
                   This is the metric the hypothesis is about.
- citation_ok      the cited evidence, taken together, contains every expected value.
- retrieval_hit    some RETRIEVED item (cited or not) contains values + labels.
                   Separates retrieval failures from answer-selection failures.
- unsupported      numbers in claims that do not appear in the cited evidence, plus
                   answering at all when the question should have been abstained on.
"""
import re

from app.answering.service import AnswerResult
from app.evaluation.dataset import Question


def norm(s: str) -> str:
    s = s.lower().replace(",", "").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def contains(text: str, needle: str) -> bool:
    """Whole-token match after normalisation: "12" does not match "1200", and "fund i"
    does not match "fund ii"."""
    pattern = r"(?<![0-9a-z.])" + re.escape(norm(needle)) + r"(?![0-9a-z]|\.[0-9])"
    return re.search(pattern, norm(text)) is not None


def _contains_all(text: str, needles: list[str]) -> bool:
    return all(contains(text, n) for n in needles)


def score(q: Question, r: AnswerResult) -> dict:
    ans = r.answer
    ev = {e.id: e for e in r.evidence}
    cited_ids = [i for c in ans.claims for i in c.evidence_ids]
    cited = [ev[i] for i in cited_ids if i in ev]

    if q.should_abstain:
        return {
            "correct": ans.abstained,
            "association": None,
            "citation_ok": None,
            "retrieval_hit": None,
            "abstained": ans.abstained,
            "unsupported": 0 if ans.abstained else 1,
        }

    needles = q.expected_values + q.evidence_labels
    return {
        "correct": (not ans.abstained) and _contains_all(ans.answer, q.expected_values),
        "association": any(_contains_all(e.text, needles) for e in cited),
        "citation_ok": bool(cited) and _contains_all(" ".join(e.text for e in cited), q.expected_values),
        "retrieval_hit": any(_contains_all(e.text, needles) for e in r.evidence),
        "abstained": ans.abstained,
        "unsupported": sum(1 for w in r.warnings if w.startswith("unsupported numbers")),
    }


def summarise(rows: list[dict]) -> dict:
    """rows: [{"question": Question, "scores": dict, "error": str|None}] for ONE configuration."""
    ans = [r for r in rows if not r["question"].should_abstain]
    abst = [r for r in rows if r["question"].should_abstain]

    def count(rs: list[dict], key: str) -> int:
        return sum(1 for r in rs if r["scores"] and r["scores"].get(key))

    return {
        "answerable_total": len(ans),
        "correct": count(ans, "correct"),
        "association": count(ans, "association"),
        "citation_ok": count(ans, "citation_ok"),
        "retrieval_hit": count(ans, "retrieval_hit"),
        "false_abstentions": count(ans, "abstained"),
        "should_abstain_total": len(abst),
        "correct_abstentions": count(abst, "abstained"),
        "answered_when_should_abstain": len(abst) - count(abst, "abstained"),
        "unsupported_number_claims": sum(r["scores"]["unsupported"] for r in ans if r["scores"]),
        "errors": sum(1 for r in rows if r["error"]),
    }
