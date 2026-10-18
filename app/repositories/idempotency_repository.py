from sqlalchemy import Connection, Row, insert, select

from app.models import idempotency_keys


def get(conn: Connection, payer_firm_id: int, key: str) -> Row | None:
    query = select(idempotency_keys.c.request_hash, idempotency_keys.c.response_body).where(
        idempotency_keys.c.payer_firm_id == payer_firm_id, idempotency_keys.c.key == key
    )
    return conn.execute(query).one_or_none()


def save(conn: Connection, payer_firm_id: int, key: str, request_hash: str, response_body: dict) -> None:
    conn.execute(
        insert(idempotency_keys).values(
            payer_firm_id=payer_firm_id, key=key, request_hash=request_hash, response_body=response_body
        )
    )
