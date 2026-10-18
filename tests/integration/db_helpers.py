from sqlalchemy import Engine, func, select

from app.models import firms, payments


def balances(engine: Engine) -> dict[int, int]:
    with engine.connect() as conn:
        return dict(conn.execute(select(firms.c.id, firms.c.balance_cents)).all())


def payment_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(payments)).scalar_one()
