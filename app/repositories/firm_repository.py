from collections.abc import Collection, Mapping

from sqlalchemy import BigInteger, Connection, Integer, Row, column, select, update, values

from app.models import firms


def get_by_uuid(conn: Connection, uuid: str) -> Row | None:
    query = select(firms.c.uuid, firms.c.name, firms.c.balance_cents).where(firms.c.uuid == uuid)
    return conn.execute(query).one_or_none()


def lock_by_uuids(conn: Connection, uuids: Collection[str]) -> list[Row]:
    """Lock the firms' rows until the transaction ends.

    Always in ascending id order, so concurrent transactions can't wait on each other in a cycle.
    """
    query = (
        select(firms.c.id, firms.c.uuid, firms.c.balance_cents)
        .where(firms.c.uuid.in_(uuids))
        .order_by(firms.c.id)
        .with_for_update()
    )
    return list(conn.execute(query))


def debit(conn: Connection, firm_id: int, cents: int) -> None:
    conn.execute(update(firms).where(firms.c.id == firm_id).values(balance_cents=firms.c.balance_cents - cents))


def credit_many(conn: Connection, credits: Mapping[int, int]) -> None:
    """Add cents to each firm id in one statement."""
    credit_rows = values(column("id", Integer), column("credit", BigInteger), name="credit_rows").data(
        sorted(credits.items())
    )
    conn.execute(
        update(firms)
        .where(firms.c.id == credit_rows.c.id)
        .values(balance_cents=firms.c.balance_cents + credit_rows.c.credit)
    )
