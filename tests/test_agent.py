"""Agent loop tests with a fake LLM and fake DB — no network or database required."""

import psycopg.errors as pgerr
import pytest

from app import agent as agent_mod
from app.agent import SQLAgent
from app.db import QueryResult
from app.llm import SQLGeneration
from app.schema import Column, Schema, Table

SCHEMA = Schema([
    Table("orders", "Orders", [Column("order_id", "integer"), Column("order_date", "timestamp"),
                               Column("status", "text", samples=["cancelled", "delivered"])]),
    Table("order_items", "Items", [Column("order_id", "integer"), Column("quantity", "integer"),
                                   Column("unit_price", "numeric")]),
])


class FakeLLM:
    def __init__(self, sqls):
        self.sqls = list(sqls)
        self.histories = []

    def generate_sql(self, question, schema_text, history):
        self.histories.append(list(history))
        return SQLGeneration(sql=self.sqls.pop(0), reasoning="test")

    def summarize(self, question, sql, columns, rows, total_rows):
        return f"{total_rows} rows"


@pytest.fixture
def fake_db(monkeypatch):
    def run_query(sql, max_rows=None):
        if "total" in sql:
            raise pgerr.UndefinedColumn('column "total" does not exist')
        if "ordrs" in sql:
            raise pgerr.UndefinedTable('relation "ordrs" does not exist')
        return QueryResult(columns=["n"], rows=[[42]], truncated=False)
    monkeypatch.setattr(agent_mod, "run_query", run_query)


def test_first_try_success(fake_db):
    llm = FakeLLM(["SELECT COUNT(*) AS n FROM orders"])
    resp = SQLAgent(llm, SCHEMA, max_retries=3).ask("how many orders?")
    assert resp.success and resp.rows == [[42]] and len(resp.attempts) == 1
    assert resp.summary == "1 rows"


def test_recovers_from_db_error(fake_db):
    llm = FakeLLM(["SELECT SUM(total) FROM orders", "SELECT COUNT(*) AS n FROM orders"])
    resp = SQLAgent(llm, SCHEMA, max_retries=3).ask("q")
    assert resp.success and len(resp.attempts) == 2
    assert resp.attempts[0].error_type == "undefined_column"
    # The second generation call must see the failed attempt with an error message.
    fed_back = llm.histories[1][0]
    assert fed_back.error_type == "undefined_column" and "total" in fed_back.sql


def test_recovers_from_guard_rejection(fake_db):
    llm = FakeLLM(["DELETE FROM orders", "SELECT COUNT(*) AS n FROM orders"])
    resp = SQLAgent(llm, SCHEMA, max_retries=3).ask("delete everything")
    assert resp.success
    assert resp.attempts[0].error_type == "not_select"
    assert "read-only" in llm.histories[1][0].hint


def test_table_hint(fake_db):
    llm = FakeLLM(["SELECT * FROM ordrs", "SELECT COUNT(*) AS n FROM orders"])
    SQLAgent(llm, SCHEMA, max_retries=3).ask("q")
    assert "orders" in llm.histories[1][0].hint


def test_gives_up_after_max_retries(fake_db):
    llm = FakeLLM(["SELECT total FROM orders"] * 3)
    resp = SQLAgent(llm, SCHEMA, max_retries=3).ask("q")
    assert not resp.success and len(resp.attempts) == 3 and resp.error


def test_no_retry_mode(fake_db):
    llm = FakeLLM(["SELECT total FROM orders", "SELECT 1 AS n"])
    resp = SQLAgent(llm, SCHEMA, max_retries=1).ask("q")
    assert not resp.success and len(resp.attempts) == 1


def test_unanswerable_stops_immediately(fake_db):
    llm = FakeLLM([""])
    resp = SQLAgent(llm, SCHEMA, max_retries=3).ask("what's the weather?")
    assert not resp.success and resp.attempts[0].error_type == "unanswerable"
    assert len(resp.attempts) == 1
