import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.error_handlers import register_exception_handlers
from app.core.exceptions import InsufficientFunds, TransientDbError, UnknownPayeeFirm
from app.schemas.bulk_payment import BulkPaymentIn
from tests.samples import SAMPLE_REQUEST as SAMPLE


@pytest.fixture
def client():
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    def echo(body: BulkPaymentIn):
        return {"total": body.total_amount_cents}

    @app.get("/raise/{kind}")
    def raise_(kind: str):
        raise {
            "insufficient": InsufficientFunds("Payer balance does not cover the total of 1325125 cents."),
            "unknown_payee": UnknownPayeeFirm("Unknown payee firms.", details=["e5f18b3c-..."]),
            "transient": TransientDbError(),
            "db_down": OperationalError("SELECT 1", {}, Exception("connection refused")),
            "bug": RuntimeError("boom"),
        }[kind]

    return TestClient(app, raise_server_exceptions=False)


def test_valid_body_passes(client):
    assert client.post("/echo", json=SAMPLE).json() == {"total": 1325125}


def test_validation_error_shape(client):
    resp = client.post("/echo", json={**SAMPLE, "payer_firm_uuid": "nope"})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["payer_firm_uuid"]


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        ("{not json", {"Content-Type": "application/json"}),
        ("[]", {"Content-Type": "application/json"}),
        ("", {"Content-Type": "application/json"}),
    ],
)
def test_malformed_body_is_validation_error(client, content, headers):
    resp = client.post("/echo", content=content, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_domain_error_shape(client):
    resp = client.get("/raise/insufficient")
    assert resp.status_code == 422
    assert resp.json() == {
        "error": {"code": "insufficient_funds", "message": "Payer balance does not cover the total of 1325125 cents."}
    }


def test_domain_error_details(client):
    resp = client.get("/raise/unknown_payee")
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == ["e5f18b3c-..."]


@pytest.mark.parametrize("kind", ["transient", "db_down"])
def test_db_errors_are_503(client, kind):
    resp = client.get(f"/raise/{kind}")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "1"
    assert resp.json()["error"]["code"] == "service_unavailable"


def test_unexpected_error_is_generic_500(client):
    resp = client.get("/raise/bug")
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "internal_error", "message": "Internal server error."}}


def test_not_found_uses_error_shape(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
