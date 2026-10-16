import io

from app.models import DocumentVersion
from tests.conftest import CORPUS, FIXTURES, upload


def test_upload_is_idempotent(client, org_a):
    r1 = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf")
    r2 = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf")
    assert r1.status_code == 202 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


def test_requires_api_key(client):
    r = client.post("/documents", files={"file": ("a.pdf", b"%PDF-1.4")})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_rejects_disguised_executable(client, org_a):
    r = upload(client, org_a["headers"], FIXTURES / "fake.pdf")
    assert r.status_code == 415 and r.json()["error"]["code"] == "CONTENT_MISMATCH"


def test_rejects_unsupported_extension(client, org_a):
    r = client.post("/documents", headers=org_a["headers"], files={"file": ("x.exe", b"MZ")})
    assert r.status_code == 415


def test_rejects_oversized_file(client, org_a):
    big = b"%PDF-1.4\n" + b"0" * 2_100_000
    r = client.post("/documents", headers=org_a["headers"], files={"file": ("big.pdf", io.BytesIO(big))})
    assert r.status_code == 413


def test_filename_never_becomes_a_path(client, org_a, factory):
    r = upload(client, org_a["headers"], CORPUS / "northwind_growth_iii.pdf", name="../../etc/passwd.pdf")
    assert r.status_code == 202
    assert r.json()["display_name"] == "passwd.pdf"
    with factory() as s:
        v = s.get(DocumentVersion, r.json()["version_id"])
        assert ".." not in v.storage_key and "passwd" not in v.storage_key


def test_error_envelope_has_request_id(client):
    r = client.get("/documents/not-a-uuid", headers={"X-API-Key": "x", "X-Request-ID": "rid-1"})
    assert r.json()["error"]["request_id"] == "rid-1"
