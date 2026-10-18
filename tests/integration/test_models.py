from sqlalchemy import inspect

from app.models import metadata


def test_models_match_database_schema(engine):
    inspector = inspect(engine)
    for table in metadata.sorted_tables:
        db_columns = {col["name"] for col in inspector.get_columns(table.name)}
        assert db_columns == set(table.columns.keys()), table.name
