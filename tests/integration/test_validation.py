import copy

import pytest

from tests.integration.db_helpers import balances, payment_count
from tests.samples import NAIR, PINECREST, SAMPLE_REQUEST

SEED_BALANCES = {1: 5000000, 2: 50000, 3: 200000}
UNKNOWN = "00000000-0000-0000-0000-000000000000"


def sample(**changes):
    body = copy.deepcopy(SAMPLE_REQUEST)
    body.update(changes)
    return body


def with_payment(**changes):
    body = copy.deepcopy(SAMPLE_REQUEST)
    body["payments"][0].update(changes)
    return body


@pytest.fixture
def assert_rejected(client, engine):
    def check(resp, code):
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == code
        assert balances(engine) == SEED_BALANCES
        assert payment_count(engine) == 0
        return resp.json()["error"]

    return check


@pytest.mark.parametrize(
    "body",
    [
        sample(extra="x"),
        sample(payer_firm_uuid="not-a-uuid"),
        sample(payments=[]),
        sample(payments=[{"amount": "1", "payee_firm_uuid": NAIR, "description": "x"}] * 1001),
        with_payment(amount=6250),
        with_payment(amount="1.234"),
        with_payment(amount="0"),
        with_payment(amount="-5"),
        with_payment(payee_firm_uuid="nope"),
        with_payment(description="   "),
        with_payment(description="x" * 1001),
        with_payment(description="a\x00b"),
        {"payer_firm_uuid": PINECREST},
        [],
    ],
)
def test_invalid_body(client, assert_rejected, body):
    error = assert_rejected(client.post("/bulk-payments", json=body), "validation_error")
    assert error["details"]


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ("{not json", "application/json"),
        ("", "application/json"),
        ("payer_firm_uuid=x", "application/x-www-form-urlencoded"),
    ],
)
def test_malformed_body(client, assert_rejected, content, content_type):
    resp = client.post("/bulk-payments", content=content, headers={"Content-Type": content_type})
    assert_rejected(resp, "validation_error")


def test_unknown_payer(client, assert_rejected):
    assert_rejected(client.post("/bulk-payments", json=sample(payer_firm_uuid=UNKNOWN)), "unknown_payer_firm")


def test_unknown_payee(client, assert_rejected):
    resp = client.post("/bulk-payments", json=with_payment(payee_firm_uuid=UNKNOWN))
    error = assert_rejected(resp, "unknown_payee_firm")
    assert error["details"] == [UNKNOWN]


def test_self_payment(client, assert_rejected):
    resp = client.post("/bulk-payments", json=with_payment(payee_firm_uuid=PINECREST))
    error = assert_rejected(resp, "self_payment")
    assert error["details"][0]["loc"] == ["payments", 0, "payee_firm_uuid"]


def test_self_payment_is_detected_case_insensitively(client, assert_rejected):
    body = with_payment(payee_firm_uuid=PINECREST.upper())
    assert_rejected(client.post("/bulk-payments", json=body), "self_payment")
