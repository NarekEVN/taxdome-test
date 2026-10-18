from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Table, Text, func

from app.models.base import metadata

payments = Table(
    "payments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("payer_firm_id", Integer, ForeignKey("firms.id"), nullable=False),
    Column("payee_firm_id", Integer, ForeignKey("firms.id"), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("description", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
