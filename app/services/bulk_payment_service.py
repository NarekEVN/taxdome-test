import hashlib
import json
import logging
import random
import time
from collections import defaultdict
from dataclasses import asdict, dataclass

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import (
    IdempotencyKeyReused,
    InsufficientFunds,
    SelfPayment,
    TransientDbError,
    UnknownPayeeFirm,
    UnknownPayerFirm,
)
from app.repositories import firm_repository, idempotency_repository, payment_repository
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
    replayed: bool = False


def create_bulk_payment(
    engine: Engine, request: BulkPaymentIn, idempotency_key: str | None = None
) -> BulkPaymentResult:
    """Apply every payment or none: one transaction, retried only on deadlock/serialization failure.

    With an idempotency key, a successful result is stored with the payments, and a repeat of the same
    request returns it (replayed=True) instead of paying again.
    """
    _reject_self_payments(request)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with engine.begin() as conn:
                return _apply(conn, request, idempotency_key)
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


def request_fingerprint(request: BulkPaymentIn) -> str:
    """Hash of the normalized request, so "300" and "300.00" or upper/lowercase UUIDs count as the same."""
    canonical = {
        "payer_firm_uuid": request.payer_firm_uuid,
        "payments": [[p.payee_firm_uuid, p.amount_cents, p.description] for p in request.payments],
    }
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


def _apply(conn: Connection, request: BulkPaymentIn, idempotency_key: str | None) -> BulkPaymentResult:
    payer_uuid = request.payer_firm_uuid
    payee_uuids = {p.payee_firm_uuid for p in request.payments}
    locked = {row.uuid: row for row in firm_repository.lock_by_uuids(conn, payee_uuids | {payer_uuid})}

    payer = locked.get(payer_uuid)
    if payer is None:
        raise UnknownPayerFirm(f"Payer firm {payer_uuid} does not exist.")

    if idempotency_key is not None:
        request_hash = request_fingerprint(request)
        stored = idempotency_repository.get(conn, payer.id, idempotency_key)
        if stored is not None:
            if stored.request_hash != request_hash:
                raise IdempotencyKeyReused("This Idempotency-Key was already used with a different request.")
            return _result_from_json(stored.response_body)
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

    result = BulkPaymentResult(
        payer_firm_uuid=payer_uuid,
        total_amount_cents=total,
        payments=[
            CreatedPayment(payment_id, p.payee_firm_uuid, p.amount_cents, p.description)
            for payment_id, p in zip(payment_ids, request.payments, strict=True)
        ],
    )
    if idempotency_key is not None:
        idempotency_repository.save(conn, payer.id, idempotency_key, request_hash, _result_to_json(result))
    return result


def _result_to_json(result: BulkPaymentResult) -> dict:
    body = asdict(result)
    del body["replayed"]
    return body


def _result_from_json(body: dict) -> BulkPaymentResult:
    return BulkPaymentResult(
        payer_firm_uuid=body["payer_firm_uuid"],
        total_amount_cents=body["total_amount_cents"],
        payments=[CreatedPayment(**p) for p in body["payments"]],
        replayed=True,
    )
