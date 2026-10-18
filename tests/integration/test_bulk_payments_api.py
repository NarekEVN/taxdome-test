import psycopg.errors
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models import payments
from app.repositories import firm_repository
from app.services import bulk_payment_service
from tests.integration.db_helpers import balances, payment_count
from tests.samples import LOPEZ, NAIR, PINECREST, SAMPLE_REQUEST

SEED_BALANCES = {1: 5000000, 2: 50000, 3: 200000}


def post(client, body):
    return client.post("/bulk-payments", json=body)


def firm_balance(client, uuid):
    return client.get(f"/firms/{uuid}").json()["balance_cents"]


def test_assignment_sample(client, engine):
    resp = post(client, SAMPLE_REQUEST)

    assert resp.status_code == 201
    assert resp.json() == {
        "payer_firm_uuid": PINECREST,
        "total_amount_cents": 1325125,
        "payments": [
            {"id": 1, "payee_firm_uuid": NAIR, "amount_cents": 625000, "description": "Overflow returns, August 2026"},
            {"id": 2, "payee_firm_uuid": NAIR, "amount_cents": 580050, "description": "Amended returns, August 2026"},
            {
                "id": 3,
                "payee_firm_uuid": LOPEZ,
                "amount_cents": 120075,
                "description": "Bookkeeping cleanup, 3 clients",
            },
        ],
    }
    assert [firm_balance(client, uuid) for uuid in (PINECREST, LOPEZ, NAIR)] == [3674875, 170075, 1405050]
    assert balances(engine) == {1: 3674875, 2: 170075, 3: 1405050}
    with engine.connect() as conn:
        rows = conn.execute(
            select(payments.c.id, payments.c.payer_firm_id, payments.c.payee_firm_id).order_by(payments.c.id)
        ).all()
    assert rows == [(1, 1, 3), (2, 1, 3), (3, 1, 2)]


def test_repeated_sample_until_funds_run_out(client, engine):
    statuses = [post(client, SAMPLE_REQUEST).status_code for _ in range(4)]

    assert statuses == [201, 201, 201, 422]
    assert balances(engine) == {1: 5000000 - 3 * 1325125, 2: 50000 + 3 * 120075, 3: 200000 + 3 * 1205050}
    assert payment_count(engine) == 9


def test_insufficient_funds(client, engine):
    body = {
        "payer_firm_uuid": LOPEZ,
        "payments": [
            {"amount": "300", "payee_firm_uuid": NAIR, "description": "a"},
            {"amount": "200.01", "payee_firm_uuid": PINECREST, "description": "b"},
        ],
    }
    resp = post(client, body)

    assert resp.status_code == 422
    assert resp.json() == {
        "error": {"code": "insufficient_funds", "message": "Payer balance does not cover the total of 50001 cents."}
    }
    assert balances(engine) == SEED_BALANCES
    assert payment_count(engine) == 0


def test_exact_balance(client, engine):
    body = {"payer_firm_uuid": LOPEZ, "payments": [{"amount": "500", "payee_firm_uuid": NAIR, "description": "a"}]}
    resp = post(client, body)

    assert resp.status_code == 201
    assert balances(engine) == {1: 5000000, 2: 0, 3: 250000}


def test_persistent_deadlock_is_503(client, engine, monkeypatch):
    def lock(conn, uuids):
        raise OperationalError("SELECT ... FOR UPDATE", {}, psycopg.errors.DeadlockDetected("deadlock detected"))

    monkeypatch.setattr(firm_repository, "lock_by_uuids", lock)
    monkeypatch.setattr(bulk_payment_service.time, "sleep", lambda _: None)
    resp = post(client, SAMPLE_REQUEST)

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "1"
    assert balances(engine) == SEED_BALANCES
