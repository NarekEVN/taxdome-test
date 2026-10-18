from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import Connection, insert

from app.models import payments


class NewPayment(NamedTuple):
    payee_firm_id: int
    amount_cents: int
    description: str


def insert_many(conn: Connection, payer_firm_id: int, new_payments: Sequence[NewPayment]) -> list[int]:
    """Insert all payments in one statement and return their ids in input order.

    sort_by_parameter_order makes SQLAlchemy guarantee RETURNING order matches the input,
    which Postgres alone does not promise for multi-row inserts.
    """
    rows = [{"payer_firm_id": payer_firm_id, **p._asdict()} for p in new_payments]
    statement = insert(payments).returning(payments.c.id, sort_by_parameter_order=True)
    return list(conn.execute(statement, rows).scalars())
