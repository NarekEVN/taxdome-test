import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import DatabaseSettings
from app.core.database import create_db_engine
from app.main import create_app
from app.schemas.bulk_payment import BulkPaymentIn
from app.services.bulk_payment_service import create_bulk_payment
from tests.integration.db_helpers import balances, payment_count
from tests.samples import SAMPLE_REQUEST

SEED_BALANCES = {1: 5000000, 2: 50000, 3: 200000}


@pytest.fixture
def short_timeouts(database_url):
    return DatabaseSettings(
        database_url=database_url, db_lock_timeout_ms=200, db_statement_timeout_ms=500, _env_file=None
    )


@pytest.fixture
def payer_row_locked(engine):
    """Hold Pinecrest's row lock in another transaction, like a slow concurrent request."""
    with engine.connect() as conn:
        conn.execute(text("SELECT id FROM firms WHERE id = 1 FOR UPDATE"))
        yield
        conn.rollback()


def test_lock_wait_fails_fast_instead_of_hanging(short_timeouts, payer_row_locked, engine):
    service_engine = create_db_engine(short_timeouts)
    started = time.monotonic()
    try:
        with pytest.raises(OperationalError) as exc_info:
            create_bulk_payment(service_engine, BulkPaymentIn.model_validate(SAMPLE_REQUEST))
    finally:
        service_engine.dispose()

    assert exc_info.value.orig.sqlstate == "55P03"  # lock_not_available
    assert time.monotonic() - started < 2
    assert balances(engine) == SEED_BALANCES
    assert payment_count(engine) == 0


def test_lock_wait_timeout_is_503(short_timeouts, payer_row_locked, engine):
    with TestClient(create_app(short_timeouts)) as client:
        resp = client.post("/bulk-payments", json=SAMPLE_REQUEST)

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "1"
    assert payment_count(engine) == 0


def test_statement_timeout_is_set(short_timeouts):
    service_engine = create_db_engine(short_timeouts)
    try:
        with service_engine.connect() as conn, pytest.raises(OperationalError) as exc_info:
            conn.execute(text("SELECT pg_sleep(2)"))
    finally:
        service_engine.dispose()
    assert exc_info.value.orig.sqlstate == "57014"  # query_canceled


def test_default_timeouts_are_applied(database_url):
    service_engine = create_db_engine(DatabaseSettings(database_url=database_url, _env_file=None))
    try:
        with service_engine.connect() as conn:
            assert conn.execute(text("SHOW lock_timeout")).scalar_one() == "5s"
            assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "15s"
    finally:
        service_engine.dispose()
