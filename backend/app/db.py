from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine

from app.config import get_settings

# Without a timeout, a connection attempt that gets no answer waits forever: the
# container then never starts and logs nothing. Failing after 10 s lets it restart.
CONNECT_ARGS = {"connect_timeout": 10}


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().sqlalchemy_url, pool_pre_ping=True, connect_args=CONNECT_ARGS
    )


def get_conn() -> Iterator[Connection]:
    """One transaction per request: committed if the handler returns, rolled back if it raises."""
    with get_engine().connect() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
