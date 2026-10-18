from sqlalchemy import BigInteger, Column, Integer, Table, Text

from app.models.base import metadata

firms = Table(
    "firms",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False),
    Column("balance_cents", BigInteger, nullable=False),
    Column("uuid", Text, nullable=False, unique=True),
)
