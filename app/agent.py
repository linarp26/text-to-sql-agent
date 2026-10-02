"""Text-to-SQL agent with an autonomous self-correction loop.

generate -> guard -> execute; on any failure the error (classified + enriched with
schema hints) is fed back to the LLM, up to `max_retries` attempts.
"""

from __future__ import annotations

import re
import time

import psycopg
from psycopg import errors as pgerr

from . import guard
from .config import get_settings
from .db import run_query
from .llm import FailedAttempt, LLMError, LLMProvider
from .models import AgentResponse, Attempt
from .schema import Schema

_PG_ERROR_TYPES: list[tuple[type[Exception], str]] = [
    (pgerr.UndefinedColumn, "undefined_column"),
    (pgerr.UndefinedTable, "undefined_table"),
    (pgerr.UndefinedFunction, "undefined_function"),
    (pgerr.SyntaxError, "syntax_error"),
    (pgerr.GroupingError, "grouping_error"),
    (pgerr.AmbiguousColumn, "ambiguous_column"),
    (pgerr.DatatypeMismatch, "type_mismatch"),
    (pgerr.InvalidTextRepresentation, "type_mismatch"),
    (pgerr.DivisionByZero, "division_by_zero"),
    (pgerr.QueryCanceled, "timeout"),
    (pgerr.ReadOnlySqlTransaction, "read_only_violation"),
    (pgerr.InsufficientPrivilege, "permission_denied"),
]


def classify_error(e: Exception) -> str:
    if isinstance(e, guard.GuardError):
        return e.kind
    for cls, name in _PG_ERROR_TYPES:
        if isinstance(e, cls):
            return name
    return "database_error"


def error_hint(error_type: str, message: str, schema: Schema) -> str | None:
    """Turn a raw DB error into an actionable hint using the schema cache."""
    if error_type == "undefined_column":
        m = re.search(r'column "?([\w.]+)"? does not exist', message)
        if m:
            ident = m.group(1)
            col = ident.split(".")[-1]
            matches = schema.suggest(col or ident)
            hint = f"Column '{ident}' does not exist."
            if matches:
                hint += f" Did you mean: {', '.join(matches)}?"
            # Table aliases hide the real table, so also list where similarly named columns live.
            owners = [f"{t.name}.{c.name}" for t in schema.tables for c in t.columns if c.name == (col or ident)]
            if owners:
                hint += f" A column with that name exists in: {', '.join(owners)}."
            return hint
    if error_type == "undefined_table":
        m = re.search(r'relation "?([\w.]+)"? does not exist', message)
        if m:
            matches = schema.suggest(m.group(1).split(".")[-1])
            return f"Valid tables: {', '.join(t.name for t in schema.tables)}." + (
                f" Closest match: {matches[0]}." if matches else "")
    if error_type == "grouping_error":
        return "Every non-aggregated column in SELECT must appear in GROUP BY."
    if error_type == "timeout":
        return "The query was too slow. Simplify it, filter earlier, or avoid cartesian joins."
    if error_type in ("read_only_violation", "permission_denied", "forbidden_operation", "not_select"):
        return "Only read-only SELECT queries are permitted."
    return None


class SQLAgent:
    def __init__(self, llm: LLMProvider, schema: Schema, max_retries: int | None = None,
                 summarize: bool = True):
        s = get_settings()
        self.llm = llm
        self.schema = schema
        self.schema_text = schema.to_prompt()
        self.max_retries = max_retries if max_retries is not None else s.max_retries
        self.row_limit = s.row_limit
        self.summary_rows = s.summary_rows
        self.summarize = summarize

    def ask(self, question: str) -> AgentResponse:
        t0 = time.perf_counter()
        attempts: list[Attempt] = []
        history: list[FailedAttempt] = []
        resp = AgentResponse(question=question, success=False)

        for n in range(1, max(1, self.max_retries) + 1):
            ta = time.perf_counter()
            sql, reasoning = None, None
            try:
                gen = self.llm.generate_sql(question, self.schema_text, history)
                sql, reasoning = gen.sql, gen.reasoning
                if not sql:
                    # Model judged the question unanswerable; retrying won't help.
                    attempts.append(Attempt(attempt=n, sql=None, reasoning=reasoning, error_type="unanswerable",
                                            error=reasoning or "Question cannot be answered from this database.",
                                            latency_ms=_ms(ta)))
                    resp.error = attempts[-1].error
                    break
                safe_sql = guard.validate(sql, self.row_limit)
                result = run_query(safe_sql, self.row_limit)
            except LLMError as e:
                attempts.append(Attempt(attempt=n, sql=sql, reasoning=reasoning, error=str(e),
                                        error_type="llm_error", latency_ms=_ms(ta)))
                resp.error = str(e)
                break
            except (guard.GuardError, psycopg.Error) as e:
                etype = classify_error(e)
                msg = str(e).strip().splitlines()[0] if str(e).strip() else etype
                attempts.append(Attempt(attempt=n, sql=sql, reasoning=reasoning, error=msg, error_type=etype,
                                        latency_ms=_ms(ta)))
                history.append(FailedAttempt(sql=sql or "", error_type=etype, error=msg,
                                             hint=error_hint(etype, msg, self.schema)))
                resp.error = msg
                continue

            attempts.append(Attempt(attempt=n, sql=safe_sql, reasoning=reasoning, latency_ms=_ms(ta)))
            resp.success, resp.error = True, None
            resp.sql, resp.columns, resp.rows = safe_sql, result.columns, result.rows
            resp.row_count, resp.truncated = len(result.rows), result.truncated
            break

        if resp.success and self.summarize:
            try:
                resp.summary = self.llm.summarize(question, resp.sql, resp.columns,
                                                  resp.rows[: self.summary_rows], resp.row_count)
            except Exception as e:  # summary is best-effort; never fail a correct query over it
                resp.summary = f"(Summary unavailable: {e})"

        resp.attempts = attempts
        resp.latency_ms = _ms(t0)
        return resp


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
