from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import Engine

from app.api.dependencies import get_engine
from app.schemas.bulk_payment import BulkPaymentIn, BulkPaymentOut
from app.services import bulk_payment_service

router = APIRouter(prefix="/bulk-payments", tags=["bulk-payments"])

IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255, pattern=r"^[\x21-\x7e]+$")
]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_bulk_payment(
    body: BulkPaymentIn,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    idempotency_key: IdempotencyKey = None,
) -> BulkPaymentOut:
    result = bulk_payment_service.create_bulk_payment(engine, body, idempotency_key)
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return BulkPaymentOut.model_validate(result, from_attributes=True)
