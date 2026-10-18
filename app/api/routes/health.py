from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from app.api.dependencies import get_connection

router = APIRouter(tags=["health"])


@router.get("/health")
def health(conn: Annotated[Connection, Depends(get_connection)]) -> dict[str, str]:
    conn.execute(text("SELECT 1"))
    return {"status": "ok"}
