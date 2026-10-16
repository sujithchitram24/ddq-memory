"""Organisation B must never see organisation A's data, through any endpoint."""
from tests.conftest import CORPUS, upload


def test_cross_tenant_access_is_404(client, org_a, org_b, process):
    doc_id = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()["id"]
    process()
    for path in ("", "/status", "/tables", "/records"):
        r = client.get(f"/documents/{doc_id}{path}", headers=org_b["headers"])
        assert r.status_code == 404, path
    assert client.post(f"/documents/{doc_id}/retry", headers=org_b["headers"]).status_code == 404
    assert client.get("/documents", headers=org_b["headers"]).json()["total"] == 0


def test_search_never_returns_other_tenants_evidence(client, org_a, org_b, process):
    doc_id = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()["id"]
    process()
    for strategy in ("flat", "structured"):
        body = {"query": "management fee", "strategy": strategy, "document_ids": [doc_id]}
        assert client.post("/search", headers=org_b["headers"], json=body).json() == []
        assert client.post("/search", headers=org_a["headers"], json=body).json() != []


def test_same_file_in_two_orgs_is_two_documents(client, org_a, org_b):
    a = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()
    b = upload(client, org_b["headers"], CORPUS / "northwind_growth_iii.pdf").json()
    assert a["id"] != b["id"]
