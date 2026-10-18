from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Connection, Engine


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_connection(request: Request) -> Iterator[Connection]:
    with get_engine(request).connect() as conn:
        yield conn
