from fastapi.testclient import TestClient

from app.main import create_app


def test_live(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_with_db(client):
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["db"] == "ok"


def test_ready_without_db_returns_503_and_leaks_nothing(settings):
    bad = settings.model_copy(update={"database_url": "postgresql+psycopg://ddq:secretpw@localhost:1/ddq"})
    with TestClient(create_app(bad)) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    assert "secretpw" not in r.text and "localhost" not in r.text


def test_request_id_is_echoed(client):
    r = client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
