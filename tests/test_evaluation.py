from app.answering.schemas import Answer
from app.answering.service import AnswerResult
from app.evaluation.dataset import Question, load_dataset
from app.evaluation.scoring import contains, score
from tests.conftest import CORPUS, upload
from tests.test_answering import ev


def test_dataset_is_valid():
    ds = load_dataset()
    assert len(ds.questions) >= 30 and sum(q.should_abstain for q in ds.questions) >= 3


def test_matching_respects_token_boundaries():
    assert contains("Net IRR 14.2%", "14.2%")
    assert not contains("Target size 1200", "12")
    assert not contains("Gross IRR 12.4%", "12")
    assert not contains("Fund II", "fund i")
    assert contains("USD 5,000,000", "usd 5000000")


def test_association_requires_value_and_label_in_one_cited_item():
    q = Question(id="t", fund="f", category="c", question="?", expected_values=["2.0%"], evidence_labels=["basis"])
    e1, e2 = ev("e1", "| Management fee | 2.0% | Committed capital |"), ev("e2", "| Fee | Rate | Basis |")
    res = AnswerResult(question="?", strategy="flat", chunk_size=50, provider="t", warnings=[], evidence=[e1, e2],
                       answer=Answer(answer="2.0%", claims=[{"text": "2.0%", "evidence_ids": ["e1", "e2"]}]))
    s = score(q, res)
    assert s["correct"] and s["citation_ok"] and not s["association"]


def test_evaluation_api(client, org_a, process):
    assert client.post("/evaluations", headers=org_a["headers"], json={}).status_code == 409
    for n in ("northwind_growth_iii.pdf", "harbor_lane_credit_ii.docx", "summit_ridge_infra_i.xlsx"):
        upload(client, org_a["headers"], CORPUS / n)
    process()
    r = client.post("/evaluations", headers=org_a["headers"], json={"chunk_sizes": [50]})
    assert r.status_code == 201
    summary = r.json()["summary"]
    assert set(summary) == {"flat/50", "structured/50"}
    assert summary["flat/50"]["answerable_total"] == 30
    got = client.get(f"/evaluations/{r.json()['id']}?include_results=true", headers=org_a["headers"]).json()
    assert len(got["results"]) == 68
