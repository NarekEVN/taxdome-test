from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection

from app.api.dependencies import get_connection
from app.repositories import firm_repository
from app.schemas.firm import FirmOut
from app.schemas.types import FirmUuid

router = APIRouter(prefix="/firms", tags=["firms"])


@router.get("/{uuid}")
def get_firm(uuid: FirmUuid, conn: Annotated[Connection, Depends(get_connection)]) -> FirmOut:
    firm = firm_repository.get_by_uuid(conn, uuid)
    if firm is None:
        raise HTTPException(status_code=404, detail="Firm not found.")
    return FirmOut.model_validate(firm)
