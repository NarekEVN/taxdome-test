from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, make_url

from app.core.config import DatabaseSettings
from app.main import create_app

DB_DIR = Path(__file__).resolve().parents[2] / "db"


class TestDatabaseSettings(BaseSettings):
    __test__ = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: str


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        url = TestDatabaseSettings().test_database_url
    except ValidationError:
        pytest.fail("TEST_DATABASE_URL is not set. Copy .env.example to .env or export it.", pytrace=False)
    if not make_url(url).database.endswith("_test"):
        pytest.fail(f"Refusing to run: test database name must end with '_test', got {url!r}.", pytrace=False)
    return url


@pytest.fixture(scope="session")
def engine(database_url):
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS idempotency_keys, payments, firms CASCADE")
        conn.exec_driver_sql((DB_DIR / "001_schema.sql").read_text())
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def seeded_db(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE payments, firms RESTART IDENTITY CASCADE")
        conn.exec_driver_sql((DB_DIR / "002_seed.sql").read_text())


@pytest.fixture
def client(database_url):
    app = create_app(DatabaseSettings(database_url=database_url, _env_file=None))
    with TestClient(app) as client:
        yield client
