from fastapi.testclient import TestClient

from app.core.config import DatabaseSettings
from app.main import create_app
from tests.samples import NAIR, PINECREST


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_when_database_is_down():
    unreachable = DatabaseSettings(database_url="postgresql+psycopg://nobody:x@127.0.0.1:1/none", _env_file=None)
    with TestClient(create_app(unreachable), raise_server_exceptions=False) as client:
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "1"


def test_get_firm(client):
    resp = client.get(f"/firms/{PINECREST}")
    assert resp.status_code == 200
    assert resp.json() == {"uuid": PINECREST, "name": "Pinecrest CPA Group", "balance_cents": 5000000}


def test_get_firm_uuid_is_case_insensitive(client):
    resp = client.get(f"/firms/{NAIR.upper()}")
    assert resp.status_code == 200
    assert resp.json()["uuid"] == NAIR


def test_get_unknown_firm(client):
    resp = client.get("/firms/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Firm not found."}}


def test_get_firm_with_malformed_uuid(client):
    resp = client.get("/firms/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
