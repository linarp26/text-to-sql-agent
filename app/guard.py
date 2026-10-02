"""Static SQL validation before anything touches the database.

This is the application-layer gate; the read-only Postgres role is the hard boundary.
Rejections raise GuardError, which the agent feeds back into its correction loop.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class GuardError(Exception):
    def __init__(self, message: str, kind: str = "guard_rejected"):
        super().__init__(message)
        self.kind = kind


# Expression types that must never appear anywhere in the tree.
_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Grant, exp.Command, exp.Into, exp.Lock, exp.Set, exp.Transaction,
    exp.Commit, exp.Rollback, exp.Copy,
)

# Functions that can read files, sleep, touch other sessions, or escape the sandbox.
_FORBIDDEN_FUNCS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "lo_import", "lo_export",
    "lo_get", "dblink", "dblink_exec", "set_config", "current_setting", "pg_advisory_lock", "txid_current",
    "query_to_xml", "pg_logdir_ls", "copy",
}

_FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}


def validate(sql: str, row_limit: int = 1000) -> str:
    """Return a safe, normalised SQL string or raise GuardError."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise GuardError("Empty query.", "empty")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        raise GuardError(f"SQL could not be parsed: {e.errors[0]['description'] if e.errors else e}",
                         "syntax_error") from e

    if len(statements) != 1:
        raise GuardError("Only a single SQL statement is allowed.", "multiple_statements")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With)):
        raise GuardError(f"Only read-only SELECT queries are allowed (got {tree.key.upper()}).", "not_select")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise GuardError(f"Forbidden operation in query: {node.key.upper()}.", "forbidden_operation")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _FORBIDDEN_FUNCS:
                raise GuardError(f"Function '{name}' is not allowed.", "forbidden_function")
        if isinstance(node, exp.Table) and node.db and node.db.lower() in _FORBIDDEN_SCHEMAS:
            raise GuardError(f"Access to system schema '{node.db}' is not allowed.", "forbidden_schema")
        if isinstance(node, exp.Table) and node.name.lower().startswith("pg_"):
            raise GuardError(f"Access to system table '{node.name}' is not allowed.", "forbidden_schema")

    # Cap result size: add an outer LIMIT if the top-level query has none.
    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(row_limit)
    elif not isinstance(tree, exp.Select) and not tree.args.get("limit"):
        return f"SELECT * FROM ({tree.sql(dialect='postgres')}) AS _q LIMIT {row_limit}"

    return tree.sql(dialect="postgres")
