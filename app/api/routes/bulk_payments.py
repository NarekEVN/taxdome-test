from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import Engine

from app.api.dependencies import get_engine
from app.schemas.bulk_payment import BulkPaymentIn, BulkPaymentOut
from app.services import bulk_payment_service

router = APIRouter(prefix="/bulk-payments", tags=["bulk-payments"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_bulk_payment(body: BulkPaymentIn, engine: Annotated[Engine, Depends(get_engine)]) -> BulkPaymentOut:
    result = bulk_payment_service.create_bulk_payment(engine, body)
    return BulkPaymentOut.model_validate(result, from_attributes=True)
