from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _pool_kwargs(db_url: str = "") -> dict:
    kwargs: dict = {
        "pool_pre_ping": True,
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "5")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
    }
    # Add timeouts for remote MySQL — without read_timeout, a hung query
    # blocks the uvicorn worker forever, freezing the entire server.
    if "mysql" in db_url or "pymysql" in db_url:
        kwargs["connect_args"] = {
            "connect_timeout": 5,
            "read_timeout": 30,
            "write_timeout": 10,
        }
    return kwargs


@lru_cache(maxsize=8)
def get_engine(db_url: str) -> Engine:
    return create_engine(db_url, **_pool_kwargs(db_url))


def get_erp_engine() -> Engine:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL must be set.")
    return get_engine(db_url)


def get_external_engine() -> Engine:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL must be set.")
    return get_engine(db_url)