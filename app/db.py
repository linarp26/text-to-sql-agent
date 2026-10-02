"""Read-only query execution.

Layers of protection here (in addition to app/guard.py):
  1. Connects as `analyst_ro`, a role with SELECT-only grants.
  2. Connections are configured read_only, so every transaction is `BEGIN READ ONLY`,
     and each query's transaction is always rolled back.
  3. A per-transaction statement_timeout bounds runaway queries.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from psycopg_pool import ConnectionPool

from .config import get_settings


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _configure(conn) -> None:
    # psycopg then opens every transaction as BEGIN READ ONLY.
    conn.read_only = True
    conn.autocommit = False


@lru_cache
def get_pool() -> ConnectionPool:
    s = get_settings()
    return ConnectionPool(s.database_url, min_size=1, max_size=5, open=True, configure=_configure)


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def run_query(sql: str, max_rows: int | None = None) -> QueryResult:
    s = get_settings()
    max_rows = max_rows or s.row_limit
    with get_pool().connection() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(s.statement_timeout_ms)}")
                cur.execute(sql)
                if cur.description is None:
                    return QueryResult(columns=[], rows=[], truncated=False)
                columns = [d.name for d in cur.description]
                fetched = cur.fetchmany(max_rows + 1)
        finally:
            conn.rollback()
    truncated = len(fetched) > max_rows
    rows = [[_jsonable(v) for v in r] for r in fetched[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)
