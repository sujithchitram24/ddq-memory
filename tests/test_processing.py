from tests.conftest import CORPUS, upload


def test_upload_process_and_inspect_all_formats(client, org_a, process):
    ids = [upload(client, org_a["headers"], CORPUS / n).json()["id"]
           for n in ("northwind_growth_iii.pdf", "harbor_lane_credit_ii.docx", "summit_ridge_infra_i.xlsx")]
    assert process() == ["succeeded"] * 3
    for doc_id in ids:
        st = client.get(f"/documents/{doc_id}/status", headers=org_a["headers"]).json()
        assert st["status"] == "succeeded" and st["run_stats"]["tables"] >= 3
        tables = client.get(f"/documents/{doc_id}/tables", headers=org_a["headers"]).json()
        assert tables["total"] >= 3 and tables["items"][0]["header"]


def test_tables_before_processing_is_409(client, org_a):
    doc_id = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()["id"]
    r = client.get(f"/documents/{doc_id}/tables", headers=org_a["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PROCESSED"


def test_records_keep_rate_and_basis_in_the_same_row(client, org_a, process):
    doc_id = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()["id"]
    process()
    facts = client.get(f"/documents/{doc_id}/records?limit=500", headers=org_a["headers"]).json()["items"]
    rate = next(f for f in facts if f["row_label"] == "Management fee (Investment Period)" and f["column_label"] == "Rate")
    basis = next(f for f in facts if f["row_key"] == rate["row_key"] and f["column_label"] == "Basis")
    assert (rate["value_raw"], rate["value_kind"], rate["value_numeric"]) == ("2.0%", "percent", 2.0)
    assert basis["value_raw"] == "Committed capital"
    assert any("0.25%" in n for n in rate["footnotes"])  # footnote marker followed to its text
    assert rate["location"]["page"] == 1 and rate["processor_version"]


def test_xlsx_records_have_exact_cell_references(client, org_a, process):
    doc_id = upload(client, org_a["headers"], CORPUS / "summit_ridge_infra_i.xlsx").json()["id"]
    process()
    facts = client.get(f"/documents/{doc_id}/records?limit=500", headers=org_a["headers"]).json()["items"]
    f = next(f for f in facts if f["row_label"] == "Net IRR" and f["column_label"] == "2024")
    assert f["location"]["sheet"] == "Performance" and f["location"]["cell"] == "C3"
    assert f["period"] == "2024"


def test_search_structured_returns_whole_row(client, org_a, process):
    upload(client, org_a["headers"], CORPUS / "harbor_lane_credit_ii.docx")
    process()
    r = client.post("/search", headers=org_a["headers"],
                    json={"query": "management fee calculated on", "strategy": "structured"})
    top = r.json()[0]
    assert top["kind"] == "fact_row"
    assert "Rate = 1.25%" in top["text"] and "Calculated on = Invested capital" in top["text"]


def test_question_both_strategies(client, org_a, process):
    upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf")
    process()
    q = "What is the management fee during the investment period and what is it calculated on?"
    for strategy in ("flat", "structured"):
        r = client.post("/questions", headers=org_a["headers"], json={"query": q, "strategy": strategy, "chunk_size": 50})
        assert r.status_code == 200
        body = r.json()
        assert body["evidence"] and (body["answer"]["abstained"] or body["answer"]["claims"])
