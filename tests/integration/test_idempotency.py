import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func, select, update

from app.core.exceptions import IdempotencyKeyReused, InsufficientFunds
from app.models import firms, idempotency_keys
from app.schemas.bulk_payment import BulkPaymentIn
from app.services.bulk_payment_service import create_bulk_payment
from tests.integration.db_helpers import balances, payment_count
from tests.samples import LOPEZ, NAIR, PINECREST, SAMPLE_REQUEST

AFTER_ONE_SAMPLE = {1: 3674875, 2: 170075, 3: 1405050}


def sample(**changes):
    body = copy.deepcopy(SAMPLE_REQUEST)
    body.update(changes)
    return BulkPaymentIn.model_validate(body)


def lopez_pays(amount):
    return BulkPaymentIn.model_validate(
        {"payer_firm_uuid": LOPEZ, "payments": [{"amount": amount, "payee_firm_uuid": NAIR, "description": "a"}]}
    )


def stored_key_count(engine):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(idempotency_keys)).scalar_one()


def test_repeat_with_same_key_pays_once(engine):
    first = create_bulk_payment(engine, sample(), "key-1")
    second = create_bulk_payment(engine, sample(), "key-1")

    assert not first.replayed
    assert second.replayed
    assert second.payments == first.payments
    assert second.total_amount_cents == first.total_amount_cents
    assert balances(engine) == AFTER_ONE_SAMPLE
    assert payment_count(engine) == 3


def test_without_key_every_request_pays(engine):
    create_bulk_payment(engine, sample())
    create_bulk_payment(engine, sample())
    assert payment_count(engine) == 6
    assert stored_key_count(engine) == 0


def test_normalized_request_counts_as_the_same(engine):
    create_bulk_payment(engine, sample(), "key-1")
    body = copy.deepcopy(SAMPLE_REQUEST)
    body["payer_firm_uuid"] = PINECREST.upper()
    body["payments"][0]["amount"] = "6250.00"
    body["payments"][1]["description"] = "  Amended returns, August 2026  "

    assert create_bulk_payment(engine, BulkPaymentIn.model_validate(body), "key-1").replayed
    assert payment_count(engine) == 3


def test_same_key_different_request_is_rejected(engine):
    create_bulk_payment(engine, sample(), "key-1")
    changed = copy.deepcopy(SAMPLE_REQUEST)
    changed["payments"][0]["amount"] = "6250.01"

    with pytest.raises(IdempotencyKeyReused):
        create_bulk_payment(engine, BulkPaymentIn.model_validate(changed), "key-1")
    assert balances(engine) == AFTER_ONE_SAMPLE
    assert payment_count(engine) == 3


def test_keys_are_scoped_to_the_payer(engine):
    create_bulk_payment(engine, lopez_pays("1"), "shared-key")
    create_bulk_payment(engine, sample(), "shared-key")
    assert payment_count(engine) == 4
    assert stored_key_count(engine) == 2


def test_replay_works_after_funds_run_out(engine):
    create_bulk_payment(engine, lopez_pays("500"), "key-1")
    assert balances(engine)[2] == 0

    assert create_bulk_payment(engine, lopez_pays("500"), "key-1").replayed
    assert payment_count(engine) == 1


def test_rejected_request_is_not_stored(engine):
    with pytest.raises(InsufficientFunds):
        create_bulk_payment(engine, lopez_pays("600"), "key-1")
    assert stored_key_count(engine) == 0

    with engine.begin() as conn:
        conn.execute(update(firms).where(firms.c.id == 2).values(balance_cents=60000))
    result = create_bulk_payment(engine, lopez_pays("600"), "key-1")
    assert not result.replayed
    assert balances(engine)[2] == 0


def test_concurrent_requests_with_same_key_pay_once(database_url):
    workers = 20
    engine = create_engine(database_url, pool_size=workers, max_overflow=0)
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait()
        return create_bulk_payment(engine, sample(), "concurrent-key").replayed

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            replayed = list(pool.map(worker, range(workers)))
        assert replayed.count(False) == 1
        assert replayed.count(True) == workers - 1
        assert balances(engine) == AFTER_ONE_SAMPLE
        assert payment_count(engine) == 3
    finally:
        engine.dispose()


def post(client, key, body=SAMPLE_REQUEST):
    return client.post("/bulk-payments", json=body, headers={"Idempotency-Key": key})


def test_api_replay(client, engine):
    first = post(client, "api-key")
    second = post(client, "api-key")

    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert balances(engine) == AFTER_ONE_SAMPLE


def test_api_key_reuse_with_different_body(client, engine):
    post(client, "api-key")
    changed = copy.deepcopy(SAMPLE_REQUEST)
    changed["payments"] = changed["payments"][:1]
    resp = post(client, "api-key", changed)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "idempotency_key_reused"
    assert payment_count(engine) == 3


@pytest.mark.parametrize("key", ["", "x" * 256, "has space", "ключ"])
def test_api_invalid_key(client, engine, key):
    headers = {"Idempotency-Key": key.encode()}  # bytes, so the non-ASCII key reaches the server as sent
    resp = client.post("/bulk-payments", json=SAMPLE_REQUEST, headers=headers)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert payment_count(engine) == 0
