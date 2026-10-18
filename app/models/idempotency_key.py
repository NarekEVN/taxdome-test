from sqlalchemy import Column, DateTime, ForeignKey, Integer, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import metadata

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("payer_firm_id", Integer, ForeignKey("firms.id"), primary_key=True),
    Column("key", Text, primary_key=True),
    Column("request_hash", Text, nullable=False),
    Column("response_body", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
