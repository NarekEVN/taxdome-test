from sqlalchemy import Engine, create_engine

from app.core.config import DatabaseSettings


def create_db_engine(settings: DatabaseSettings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_s,
        connect_args={
            "options": f"-c lock_timeout={settings.db_lock_timeout_ms}"
            f" -c statement_timeout={settings.db_statement_timeout_ms}"
        },
    )
