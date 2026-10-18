from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.error_handlers import register_exception_handlers
from app.api.routes import firms, health
from app.core.config import DatabaseSettings, get_database_settings
from app.core.database import create_db_engine


def create_app(database_settings: DatabaseSettings | None = None) -> FastAPI:
    engine = create_db_engine(database_settings or get_database_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="Bulk Firm Payment Service", lifespan=lifespan)
    app.state.engine = engine
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(firms.router)
    return app
