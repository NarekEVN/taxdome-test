import logging
import random
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, func, select, update

from app.core.exceptions import InsufficientFunds
from app.models import firms, payments
from app.repositories import firm_repository
from app.schemas.bulk_payment import BulkPaymentIn
from app.services.bulk_payment_service import create_bulk_payment
from tests.integration.db_helpers import balances, payment_count
from tests.samples import LOPEZ, NAIR, PINECREST

FIRM_UUIDS = {1: PINECREST, 2: LOPEZ, 3: NAIR}
MAX_WORKERS = 50


@pytest.fixture
def pool_engine(database_url):
    engine = create_engine(database_url, pool_size=MAX_WORKERS, max_overflow=0)
    yield engine
    engine.dispose()


def bulk(payer, payments_):
    return BulkPaymentIn.model_validate(
        {
            "payer_firm_uuid": payer,
            "payments": [{"amount": a, "payee_firm_uuid": p, "description": "concurrent"} for a, p in payments_],
        }
    )


def run_concurrently(engine, requests):
    """Run every request at once, each on its own connection (like separate app instances).

    Returns "ok" or the exception class name for each request.
    """
    barrier = threading.Barrier(len(requests))

    def worker(request):
        barrier.wait()
        try:
            create_bulk_payment(engine, request)
            return "ok"
        except Exception as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return list(pool.map(worker, requests))


def set_balance(engine, firm_id, cents):
    with engine.begin() as conn:
        conn.execute(update(firms).where(firms.c.id == firm_id).values(balance_cents=cents))


def sum_by(engine, firm_column):
    with engine.connect() as conn:
        query = select(firm_column, func.sum(payments.c.amount_cents)).group_by(firm_column)
        return dict(conn.execute(query).all())


def test_no_overdraft(pool_engine):
    set_balance(pool_engine, 2, 10000)
    outcomes = run_concurrently(pool_engine, [bulk(LOPEZ, [("10", NAIR)]) for _ in range(20)])

    assert outcomes.count("ok") == 10
    assert outcomes.count(InsufficientFunds.__name__) == 10
    assert balances(pool_engine)[2] == 0
    assert payment_count(pool_engine) == 10


def test_no_deadlock_when_firms_pay_each_other(pool_engine, caplog):
    forward = bulk(PINECREST, [("1", NAIR), ("1", LOPEZ)])
    backward = bulk(NAIR, [("2", PINECREST), ("1", LOPEZ)])
    requests = [forward if i % 2 else backward for i in range(MAX_WORKERS)]

    with caplog.at_level(logging.WARNING):
        outcomes = run_concurrently(pool_engine, requests)

    assert outcomes == ["ok"] * MAX_WORKERS
    assert "retrying bulk payment" not in caplog.text
    n = MAX_WORKERS // 2
    assert balances(pool_engine) == {1: 5000000 - 200 * n + 200 * n, 2: 50000 + 200 * n, 3: 200000 + 100 * n - 300 * n}


def test_money_is_conserved(pool_engine):
    rng = random.Random(42)
    requests = []
    for _ in range(MAX_WORKERS):
        payer_id = rng.choice(list(FIRM_UUIDS))
        payee_ids = [i for i in FIRM_UUIDS if i != payer_id]
        entries = [
            (f"{rng.randint(1, 5000)}.{rng.randint(0, 99):02d}", FIRM_UUIDS[rng.choice(payee_ids)])
            for _ in range(rng.randint(1, 5))
        ]
        requests.append(bulk(FIRM_UUIDS[payer_id], entries))
    initial = balances(pool_engine)

    outcomes = run_concurrently(pool_engine, requests)

    assert set(outcomes) <= {"ok", InsufficientFunds.__name__}
    assert "ok" in outcomes
    final = balances(pool_engine)
    assert sum(final.values()) == sum(initial.values())
    assert min(final.values()) >= 0
    debits = sum_by(pool_engine, payments.c.payer_firm_id)
    credits = sum_by(pool_engine, payments.c.payee_firm_id)
    for firm_id in FIRM_UUIDS:
        assert initial[firm_id] + credits.get(firm_id, 0) - debits.get(firm_id, 0) == final[firm_id]


def test_rows_are_locked_in_id_order(engine):
    # The tests above can't tell sorted from unsorted locking: with one statement over 3 rows, every
    # transaction scans in the same order anyway. So check the plan: LockRows must sit on top of the Sort.
    statements = []

    def capture(conn, cursor, statement, params, context, executemany):
        statements.append((statement, params))

    with engine.begin() as conn:
        event.listen(conn, "before_cursor_execute", capture)
        firm_repository.lock_by_uuids(conn, [NAIR, PINECREST, LOPEZ])
        event.remove(conn, "before_cursor_execute", capture)
        statement, params = statements[0]
        plan = [row[0].strip() for row in conn.exec_driver_sql("EXPLAIN " + statement, params)]

    assert plan[0].startswith("LockRows")
    assert plan[1].lstrip("-> ").startswith("Sort")
    assert plan[2] == "Sort Key: id"
