"""Failure injection: duplicate work, worker crashes, lost leases, permanent and transient errors."""
import threading

from sqlalchemy import func, select, text

from app.config import PROCESSOR_VERSION
from app.jobs import queue
from app.models import FinancialFact, Job, ProcessingRun
from app.processing import pipeline
from app.processing.pipeline import LeaseLost, load_and_extract, persist
from app.worker import drain
from tests.conftest import CORPUS, FIXTURES, upload


def _counts(factory):
    with factory() as s:
        return (s.scalar(select(func.count()).select_from(ProcessingRun)),
                s.scalar(select(func.count()).select_from(FinancialFact)))


def test_enqueue_is_idempotent(client, org_a, factory):
    v = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()["version_id"]
    with factory() as s:
        j1 = queue.enqueue(s, org_id=org_a["org_id"], version_id=v, processor_version=PROCESSOR_VERSION, max_attempts=3)
        j2 = queue.enqueue(s, org_id=org_a["org_id"], version_id=v, processor_version=PROCESSOR_VERSION, max_attempts=3)
        s.commit()
        assert j1 == j2 and s.scalar(select(func.count()).select_from(Job)) == 1


def test_concurrent_workers_process_each_job_once(client, org_a, factory, storage):
    # Upload copies of the corpus with a byte appended so each is a distinct version.
    for i in range(6):
        for name in ("northwind_growth_iii.pdf", "harbor_lane_credit_ii.docx", "summit_ridge_infra_i.xlsx"):
            data = (CORPUS / name).read_bytes()
            if name.endswith(".pdf"):
                data += f"\n%{i}".encode()
            else:
                import io, zipfile
                buf = io.BytesIO(data)
                with zipfile.ZipFile(buf, "a") as z:
                    z.writestr(f"customXml/n{i}.xml", "<x/>")
                data = buf.getvalue()
            client.post("/documents", headers=org_a["headers"], files={"file": (f"{i}-{name}", data)})
    results: list[list[str]] = []
    threads = [threading.Thread(target=lambda w=w: results.append(drain(factory, storage, worker_id=f"w{w}"))) for w in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    all_outcomes = [o for r in results for o in r]
    assert all_outcomes.count("succeeded") == 18 and len(all_outcomes) == 18
    assert _counts(factory)[0] == 18  # exactly one run per version


def test_worker_crash_after_extraction_is_recovered(client, org_a, factory, storage):
    """Failure injection #1: the worker claims the job, extracts, then dies before commit."""
    upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf")
    with factory() as s:
        job = queue.claim(s, "doomed-worker", lease_seconds=300)
    with factory() as s:
        load_and_extract(s, storage, job.version_id)  # ... and then the process is killed.
    assert _counts(factory) == (0, 0)  # nothing partial was written
    with factory() as s:  # simulate the lease expiring
        s.execute(text("UPDATE jobs SET lease_until = now() - interval '1 second'"))
        s.commit()
    assert drain(factory, storage, worker_id="rescuer") == ["succeeded"]
    runs, facts = _counts(factory)
    assert runs == 1 and facts > 0
    with factory() as s:
        j = s.scalars(select(Job)).one()
        assert j.attempts == 2 and j.status == "succeeded"


def test_zombie_worker_cannot_commit_after_losing_lease(client, org_a, factory, storage):
    upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf")
    with factory() as s:
        job = queue.claim(s, "slow-worker", lease_seconds=300)
    with factory() as s:
        doc = load_and_extract(s, storage, job.version_id)
    with factory() as s:  # another worker took over meanwhile
        s.execute(text("UPDATE jobs SET locked_by = 'other-worker'"))
        s.commit()
    with factory() as s:
        try:
            persist(s, job, "slow-worker", doc)
            raise AssertionError("expected LeaseLost")
        except LeaseLost:
            pass
    assert _counts(factory) == (0, 0)


def test_scanned_pdf_fails_permanently_and_cannot_be_retried(client, org_a, process):
    doc_id = upload(client, org_a["headers"], FIXTURES / "scanned.pdf").json()["id"]
    assert process() == ["failed"]
    st = client.get(f"/documents/{doc_id}/status", headers=org_a["headers"]).json()
    assert st["status"] == "needs_ocr" and st["last_error_code"] == "NO_TEXT_LAYER" and st["attempts"] == 1
    assert client.post(f"/documents/{doc_id}/retry", headers=org_a["headers"]).status_code == 409


def test_malformed_spreadsheet_is_processed_with_flags_not_crash(client, org_a, process):
    """Failure injection #2: spreadsheet with a blank header row."""
    doc_id = upload(client, org_a["headers"], FIXTURES / "no_header.xlsx").json()["id"]
    assert process() == ["succeeded"]
    t = client.get(f"/documents/{doc_id}/tables", headers=org_a["headers"]).json()["items"][0]
    assert "missing_header" in t["quality_flags"]
    facts = client.get(f"/documents/{doc_id}/records", headers=org_a["headers"]).json()["items"]
    assert {f["column_label"] for f in facts} == {"column_2", "column_3"}  # placeholders, never invented names


def test_transient_errors_retry_then_dead_then_manual_retry(client, org_a, factory, storage, monkeypatch):
    doc_id = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf").json()["id"]
    calls = {"n": 0}
    real = pipeline.load_and_extract

    def flaky(*a, **k):
        calls["n"] += 1
        raise ConnectionError("storage timeout")

    monkeypatch.setattr("app.worker.load_and_extract", flaky)
    outcomes = []
    for _ in range(3):
        outcomes += drain(factory, storage)
        with factory() as s:  # skip the backoff wait
            s.execute(text("UPDATE jobs SET run_after = now()"))
            s.commit()
    assert outcomes == ["queued", "queued", "dead"] and calls["n"] == 3
    st = client.get(f"/documents/{doc_id}/status", headers=org_a["headers"]).json()
    assert st["job_status"] == "dead" and st["last_error_code"] == "UNEXPECTED"

    monkeypatch.setattr("app.worker.load_and_extract", real)
    assert client.post(f"/documents/{doc_id}/retry", headers=org_a["headers"]).status_code == 202
    assert drain(factory, storage) == ["succeeded"]
