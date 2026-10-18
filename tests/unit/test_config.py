import pytest
from pydantic import ValidationError

from app.core.config import ApiSettings, DatabaseSettings


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        DatabaseSettings(_env_file=None)


def test_database_url_comes_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db-host:5432/x")
    assert DatabaseSettings(_env_file=None).database_url == "postgresql+psycopg://u:p@db-host:5432/x"


def test_api_settings_do_not_need_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert ApiSettings(_env_file=None).max_payments_per_request == 1000
