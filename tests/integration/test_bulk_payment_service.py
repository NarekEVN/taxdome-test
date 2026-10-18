import logging

import psycopg.errors
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.exceptions import (
    InsufficientFunds,
    SelfPayment,
    TransientDbError,
    UnknownPayeeFirm,
    UnknownPayerFirm,
)
from app.models import payments
from app.repositories import firm_repository
from app.schemas.bulk_payment import BulkPaymentIn
from app.services import bulk_payment_service
from app.services.bulk_payment_service import create_bulk_payment
from tests.integration.db_helpers import balances, payment_count
from tests.samples import LOPEZ, NAIR, PINECREST, SAMPLE_REQUEST

SEED_BALANCES = {1: 5000000, 2: 50000, 3: 200000}
UNKNOWN = "00000000-0000-0000-0000-000000000000"


def bulk(payer, *payments):
    return BulkPaymentIn.model_validate(
        {
            "payer_firm_uuid": payer,
            "payments": [{"amount": a, "payee_firm_uuid": p, "description": d} for a, p, d in payments],
        }
    )


def assert_nothing_written(engine):
    assert balances(engine) == SEED_BALANCES
    assert payment_count(engine) == 0


def test_assignment_sample(engine):
    result = create_bulk_payment(engine, BulkPaymentIn.model_validate(SAMPLE_REQUEST))

    assert balances(engine) == {1: 3674875, 2: 170075, 3: 1405050}
    assert result.total_amount_cents == 1325125
    assert [(p.payee_firm_uuid, p.amount_cents) for p in result.payments] == [
        (NAIR, 625000),
        (NAIR, 580050),
        (LOPEZ, 120075),
    ]
    with engine.connect() as conn:
        rows = conn.execute(
            select(payments.c.id, payments.c.payer_firm_id, payments.c.payee_firm_id, payments.c.amount_cents).order_by(
                payments.c.id
            )
        ).all()
    assert rows == [(p.id, 1, payee_id, p.amount_cents) for p, payee_id in zip(result.payments, [3, 3, 2], strict=True)]


def test_returned_ids_match_request_order(engine):
    request = bulk(PINECREST, *[("1", NAIR if i % 2 else LOPEZ, f"payment {i}") for i in range(200)])
    result = create_bulk_payment(engine, request)

    with engine.connect() as conn:
        stored = dict(conn.execute(select(payments.c.id, payments.c.description)).all())
    assert [stored[p.id] for p in result.payments] == [f"payment {i}" for i in range(200)]


def test_exact_balance_empties_payer(engine):
    create_bulk_payment(engine, bulk(LOPEZ, ("300", NAIR, "a"), ("200", PINECREST, "b")))
    assert balances(engine) == {1: 5020000, 2: 0, 3: 230000}


def test_insufficient_funds_writes_nothing(engine):
    with pytest.raises(InsufficientFunds, match="50001 cents"):
        create_bulk_payment(engine, bulk(LOPEZ, ("300", NAIR, "a"), ("200.01", PINECREST, "b")))
    assert_nothing_written(engine)


def test_unknown_payer(engine):
    with pytest.raises(UnknownPayerFirm):
        create_bulk_payment(engine, bulk(UNKNOWN, ("1", NAIR, "a")))
    assert_nothing_written(engine)


def test_unknown_payees_are_listed(engine):
    other = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(UnknownPayeeFirm) as exc_info:
        create_bulk_payment(engine, bulk(PINECREST, ("1", NAIR, "a"), ("1", other, "b"), ("1", UNKNOWN, "c")))
    assert exc_info.value.details == [UNKNOWN, other]
    assert_nothing_written(engine)


def test_self_payment(engine):
    with pytest.raises(SelfPayment) as exc_info:
        create_bulk_payment(engine, bulk(PINECREST, ("1", NAIR, "a"), ("1", PINECREST, "b")))
    assert exc_info.value.details[0]["loc"] == ["payments", 1, "payee_firm_uuid"]
    assert_nothing_written(engine)


def test_balance_check_constraint_blocks_overdraft_even_without_service_check(engine):
    with engine.connect() as conn, pytest.raises(IntegrityError, match="firms_balance_cents_check"):
        firm_repository.debit(conn, 2, 50001)


def deadlock():
    return OperationalError("SELECT ... FOR UPDATE", {}, psycopg.errors.DeadlockDetected("deadlock detected"))


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(bulk_payment_service.time, "sleep", lambda _: None)


@pytest.fixture
def failing_lock(monkeypatch):
    """Make lock_by_uuids raise a deadlock for the first `failures` calls."""
    calls = []
    real = firm_repository.lock_by_uuids

    def install(failures):
        def lock(conn, uuids):
            calls.append(1)
            if len(calls) <= failures:
                raise deadlock()
            return real(conn, uuids)

        monkeypatch.setattr(firm_repository, "lock_by_uuids", lock)
        return calls

    return install


def test_retries_after_deadlock(engine, no_sleep, failing_lock, caplog):
    calls = failing_lock(failures=2)
    with caplog.at_level(logging.WARNING):
        create_bulk_payment(engine, BulkPaymentIn.model_validate(SAMPLE_REQUEST))
    assert len(calls) == 3
    assert caplog.text.count("retrying bulk payment") == 2
    assert balances(engine) == {1: 3674875, 2: 170075, 3: 1405050}


def test_gives_up_after_max_attempts(engine, no_sleep, failing_lock):
    calls = failing_lock(failures=99)
    with pytest.raises(TransientDbError):
        create_bulk_payment(engine, BulkPaymentIn.model_validate(SAMPLE_REQUEST))
    assert len(calls) == bulk_payment_service.MAX_ATTEMPTS
    assert_nothing_written(engine)


def test_business_rejections_are_not_retried(engine, no_sleep, failing_lock):
    calls = failing_lock(failures=0)
    with pytest.raises(InsufficientFunds):
        create_bulk_payment(engine, bulk(LOPEZ, ("1000", NAIR, "a")))
    assert len(calls) == 1


def test_other_database_errors_are_not_retried(engine, no_sleep, monkeypatch):
    calls = []

    def lock(conn, uuids):
        calls.append(1)
        raise OperationalError("SELECT 1", {}, psycopg.errors.AdminShutdown("terminating connection"))

    monkeypatch.setattr(firm_repository, "lock_by_uuids", lock)
    with pytest.raises(OperationalError):
        create_bulk_payment(engine, BulkPaymentIn.model_validate(SAMPLE_REQUEST))
    assert len(calls) == 1
