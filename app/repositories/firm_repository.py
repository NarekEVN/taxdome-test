from sqlalchemy import Connection, Row, select

from app.models import firms


def get_by_uuid(conn: Connection, uuid: str) -> Row | None:
    query = select(firms.c.uuid, firms.c.name, firms.c.balance_cents).where(firms.c.uuid == uuid)
    return conn.execute(query).one_or_none()
