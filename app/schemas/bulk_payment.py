from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_api_settings
from app.schemas.types import Amount, Description, FirmUuid
from app.utils.money import parse_amount_to_cents


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Amount
    payee_firm_uuid: FirmUuid
    description: Description

    @property
    def amount_cents(self) -> int:
        return parse_amount_to_cents(self.amount)


class BulkPaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payer_firm_uuid: FirmUuid
    payments: list[PaymentIn] = Field(min_length=1)

    @field_validator("payments")
    @classmethod
    def _limit_payment_count(cls, payments: list[PaymentIn]) -> list[PaymentIn]:
        limit = get_api_settings().max_payments_per_request
        if len(payments) > limit:
            raise ValueError(f"at most {limit} payments per request")
        return payments

    @property
    def total_amount_cents(self) -> int:
        return sum(p.amount_cents for p in self.payments)


class PaymentOut(BaseModel):
    id: int
    payee_firm_uuid: str
    amount_cents: int
    description: str


class BulkPaymentOut(BaseModel):
    payer_firm_uuid: str
    total_amount_cents: int
    payments: list[PaymentOut]
