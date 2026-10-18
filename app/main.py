from fastapi import FastAPI

from app.api.error_handlers import register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Bulk Firm Payment Service")
    register_exception_handlers(app)
    return app


app = create_app()
