"""Schema introspection → compact, LLM-friendly schema description.

Reads table/column comments (COMMENT ON) so the prompt carries business meaning,
plus foreign keys and sample values of low-cardinality text columns (e.g. status
enums), which sharply reduces hallucinated literals like status = 'complete'.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from functools import lru_cache

from .db import get_pool

LOW_CARDINALITY = 12


@dataclass
class Column:
    name: str
    type: str
    description: str | None = None
    fk: str | None = None
    samples: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str
    description: str | None
    columns: list[Column]


@dataclass
class Schema:
    tables: list[Table]

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)

    def to_prompt(self) -> str:
        lines = []
        for t in self.tables:
            lines.append(f"TABLE {t.name}" + (f"  -- {t.description}" if t.description else ""))
            for c in t.columns:
                bits = [f"  {c.name} {c.type}"]
                if c.fk:
                    bits.append(f"FK -> {c.fk}")
                if c.description:
                    bits.append(f"-- {c.description}")
                if c.samples:
                    bits.append("values: " + ", ".join(repr(s) for s in c.samples))
                lines.append("  ".join(bits))
            lines.append("")
        return "\n".join(lines).strip()

    def to_dict(self) -> dict:
        return {"tables": [{"name": t.name, "description": t.description,
                            "columns": [{"name": c.name, "type": c.type, "description": c.description}
                                        for c in t.columns]} for t in self.tables]}

    def suggest(self, identifier: str, table: str | None = None) -> list[str]:
        """Fuzzy-match an unknown identifier against known tables/columns (for error hints)."""
        if table and (t := self.table(table)):
            candidates = [c.name for c in t.columns]
        else:
            candidates = [t.name for t in self.tables] + [f"{t.name}.{c.name}" for t in self.tables for c in t.columns]
        return difflib.get_close_matches(identifier, candidates, n=3, cutoff=0.5)


_COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.data_type,
       col_description(format('public.%I', c.table_name)::regclass, c.ordinal_position) AS col_desc,
       obj_description(format('public.%I', c.table_name)::regclass, 'pg_class') AS tbl_desc
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""

# pg_constraint rather than information_schema: the latter hides constraints from
# non-owner roles, and the agent introspects as the read-only role.
_FK_SQL = """
SELECT src.relname, sa.attname, dst.relname, da.attname
FROM pg_constraint c
JOIN pg_class src ON src.oid = c.conrelid
JOIN pg_class dst ON dst.oid = c.confrelid
JOIN pg_namespace n ON n.oid = src.relnamespace
JOIN pg_attribute sa ON sa.attrelid = c.conrelid AND sa.attnum = c.conkey[1]
JOIN pg_attribute da ON da.attrelid = c.confrelid AND da.attnum = c.confkey[1]
WHERE c.contype = 'f' AND n.nspname = 'public'
"""


@lru_cache
def get_schema() -> Schema:
    with get_pool().connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(_COLUMNS_SQL)
            col_rows = cur.fetchall()
            cur.execute(_FK_SQL)
            fks = {(r[0], r[1]): f"{r[2]}.{r[3]}" for r in cur.fetchall()}

            tables: dict[str, Table] = {}
            for tname, cname, ctype, cdesc, tdesc in col_rows:
                t = tables.setdefault(tname, Table(tname, tdesc, []))
                t.columns.append(Column(cname, ctype, cdesc, fks.get((tname, cname))))

            # Sample distinct values for short text columns that look like enums.
            for t in tables.values():
                for c in t.columns:
                    if c.type != "text" or c.name in ("comment", "email", "first_name", "last_name", "postal_code"):
                        continue
                    cur.execute(f'SELECT DISTINCT "{c.name}" FROM "{t.name}" WHERE "{c.name}" IS NOT NULL '
                                f"LIMIT {LOW_CARDINALITY + 1}")
                    vals = [r[0] for r in cur.fetchall()]
                    if len(vals) <= LOW_CARDINALITY:
                        c.samples = sorted(vals)
        finally:
            conn.rollback()
    return Schema(list(tables.values()))
