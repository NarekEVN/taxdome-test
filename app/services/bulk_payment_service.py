import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import (
    InsufficientFunds,
    SelfPayment,
    TransientDbError,
    UnknownPayeeFirm,
    UnknownPayerFirm,
)
from app.repositories import firm_repository, payment_repository
from app.repositories.payment_repository import NewPayment
from app.schemas.bulk_payment import BulkPaymentIn

logger = logging.getLogger(__name__)

DEADLOCK_DETECTED = "40P01"
SERIALIZATION_FAILURE = "40001"
TRANSIENT_SQLSTATES = {DEADLOCK_DETECTED, SERIALIZATION_FAILURE}
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class CreatedPayment:
    id: int
    payee_firm_uuid: str
    amount_cents: int
    description: str


@dataclass(frozen=True)
class BulkPaymentResult:
    payer_firm_uuid: str
    total_amount_cents: int
    payments: list[CreatedPayment]


def create_bulk_payment(engine: Engine, request: BulkPaymentIn) -> BulkPaymentResult:
    """Apply every payment or none: one transaction, retried only on deadlock/serialization failure."""
    _reject_self_payments(request)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with engine.begin() as conn:
                return _apply(conn, request)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) not in TRANSIENT_SQLSTATES:
                raise
            if attempt == MAX_ATTEMPTS:
                raise TransientDbError(f"bulk payment failed after {attempt} attempts") from exc
            logger.warning("retrying bulk payment after transient error (attempt %d): %s", attempt, exc.orig)
            time.sleep(random.uniform(0.01, 0.05))
    raise AssertionError("unreachable")


def _reject_self_payments(request: BulkPaymentIn) -> None:
    details = [
        {"loc": ["payments", i, "payee_firm_uuid"], "msg": "payee must differ from payer"}
        for i, payment in enumerate(request.payments)
        if payment.payee_firm_uuid == request.payer_firm_uuid
    ]
    if details:
        raise SelfPayment("A firm cannot pay itself.", details)


def _apply(conn: Connection, request: BulkPaymentIn) -> BulkPaymentResult:
    payer_uuid = request.payer_firm_uuid
    payee_uuids = {p.payee_firm_uuid for p in request.payments}
    locked = {row.uuid: row for row in firm_repository.lock_by_uuids(conn, payee_uuids | {payer_uuid})}

    payer = locked.get(payer_uuid)
    if payer is None:
        raise UnknownPayerFirm(f"Payer firm {payer_uuid} does not exist.")
    missing = sorted(payee_uuids - locked.keys())
    if missing:
        raise UnknownPayeeFirm("Some payee firms do not exist.", missing)
    total = request.total_amount_cents
    if payer.balance_cents < total:
        raise InsufficientFunds(f"Payer balance does not cover the total of {total} cents.")

    new_payments = [NewPayment(locked[p.payee_firm_uuid].id, p.amount_cents, p.description) for p in request.payments]
    payment_ids = payment_repository.insert_many(conn, payer.id, new_payments)

    credits: dict[int, int] = defaultdict(int)
    for payment in new_payments:
        credits[payment.payee_firm_id] += payment.amount_cents
    firm_repository.debit(conn, payer.id, total)
    firm_repository.credit_many(conn, credits)

    return BulkPaymentResult(
        payer_firm_uuid=payer_uuid,
        total_amount_cents=total,
        payments=[
            CreatedPayment(payment_id, p.payee_firm_uuid, p.amount_cents, p.description)
            for payment_id, p in zip(payment_ids, request.payments, strict=True)
        ],
    )
