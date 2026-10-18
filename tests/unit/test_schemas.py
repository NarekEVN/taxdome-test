import copy

import pytest
from pydantic import ValidationError

from app.schemas.bulk_payment import BulkPaymentIn
from tests.samples import NAIR
from tests.samples import PINECREST as PAYER
from tests.samples import SAMPLE_REQUEST as SAMPLE


def sample(**changes):
    body = copy.deepcopy(SAMPLE)
    body.update(changes)
    return body


def with_payment(**changes):
    body = copy.deepcopy(SAMPLE)
    body["payments"][0].update(changes)
    return body


def error_locs(body):
    with pytest.raises(ValidationError) as exc_info:
        BulkPaymentIn.model_validate(body)
    return [err["loc"] for err in exc_info.value.errors()]


def test_sample_request_is_valid():
    req = BulkPaymentIn.model_validate(SAMPLE)
    assert [p.amount_cents for p in req.payments] == [625000, 580050, 120075]
    assert req.total_amount_cents == 1325125


def test_uuids_are_normalized_to_lowercase():
    body = sample(payer_firm_uuid=PAYER.upper())
    body["payments"][0]["payee_firm_uuid"] = NAIR.upper()
    req = BulkPaymentIn.model_validate(body)
    assert req.payer_firm_uuid == PAYER
    assert req.payments[0].payee_firm_uuid == NAIR


def test_description_is_stripped():
    req = BulkPaymentIn.model_validate(with_payment(description="  Referral fee  "))
    assert req.payments[0].description == "Referral fee"


def test_duplicate_payees_are_allowed():
    req = BulkPaymentIn.model_validate(SAMPLE)
    assert [p.payee_firm_uuid for p in req.payments].count(NAIR) == 2


def test_max_payments_accepted():
    payment = {"amount": "1", "payee_firm_uuid": NAIR, "description": "x"}
    assert len(BulkPaymentIn.model_validate(sample(payments=[payment] * 1000)).payments) == 1000


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        (sample(extra="x"), ("extra",)),
        (sample(payer_firm_uuid="not-a-uuid"), ("payer_firm_uuid",)),
        (sample(payer_firm_uuid="{3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41}"), ("payer_firm_uuid",)),
        (sample(payer_firm_uuid="3f1c9a2e7b4d4c1e9a552d8e6f0b7c41"), ("payer_firm_uuid",)),
        (sample(payer_firm_uuid=123), ("payer_firm_uuid",)),
        (sample(payments=[]), ("payments",)),
        (sample(payments={}), ("payments",)),
        (sample(payments=[{"amount": "1", "payee_firm_uuid": NAIR, "description": "x"}] * 1001), ("payments",)),
        (with_payment(amount=1200.75), ("payments", 0, "amount")),
        (with_payment(amount=1200), ("payments", 0, "amount")),
        (with_payment(amount="1.234"), ("payments", 0, "amount")),
        (with_payment(amount="0"), ("payments", 0, "amount")),
        (with_payment(amount="-5"), ("payments", 0, "amount")),
        (with_payment(payee_firm_uuid="nope"), ("payments", 0, "payee_firm_uuid")),
        (with_payment(description=""), ("payments", 0, "description")),
        (with_payment(description="   "), ("payments", 0, "description")),
        (with_payment(description="x" * 1001), ("payments", 0, "description")),
        (with_payment(description=None), ("payments", 0, "description")),
        (with_payment(note="x"), ("payments", 0, "note")),
    ],
)
def test_invalid_requests_are_rejected(body, loc):
    assert loc in error_locs(body)


@pytest.mark.parametrize("field", ["amount", "payee_firm_uuid", "description"])
def test_missing_payment_field(field):
    body = copy.deepcopy(SAMPLE)
    del body["payments"][0][field]
    assert ("payments", 0, field) in error_locs(body)


@pytest.mark.parametrize("field", ["payer_firm_uuid", "payments"])
def test_missing_top_level_field(field):
    body = copy.deepcopy(SAMPLE)
    del body[field]
    assert (field,) in error_locs(body)
