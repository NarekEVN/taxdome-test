from sqlalchemy import Engine, create_engine

from app.core.config import DatabaseSettings


def create_db_engine(settings: DatabaseSettings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
