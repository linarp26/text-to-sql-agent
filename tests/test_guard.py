import pytest

from app.guard import GuardError, validate


@pytest.mark.parametrize("sql,kind", [
    ("DROP TABLE orders", "not_select"),
    ("DELETE FROM orders WHERE order_id = 1", "not_select"),
    ("UPDATE products SET unit_price = 0", "not_select"),
    ("INSERT INTO customers (first_name) VALUES ('x')", "not_select"),
    ("SELECT 1; DROP TABLE orders", "multiple_statements"),
    ("SELECT pg_sleep(10)", "forbidden_function"),
    ("SELECT pg_read_file('/etc/passwd')", "forbidden_function"),
    ("SELECT * FROM pg_catalog.pg_roles", "forbidden_schema"),
    ("SELECT * FROM pg_user", "forbidden_schema"),
    ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "forbidden_operation"),
    ("SELECT * INTO new_table FROM orders", "forbidden_operation"),
    ("", "empty"),
    ("SELEC * FRM orders", "syntax_error|not_select"),  # sqlglot may parse garbage leniently; any rejection is fine
])
def test_rejects_unsafe(sql, kind):
    with pytest.raises(GuardError) as ei:
        validate(sql)
    assert ei.value.kind in kind.split("|"), (sql, ei.value.kind, str(ei.value))


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM orders",
    "WITH m AS (SELECT date_trunc('month', order_date) AS m, COUNT(*) c FROM orders GROUP BY 1) SELECT * FROM m",
    "SELECT customer_id, RANK() OVER (ORDER BY COUNT(*) DESC) FROM orders GROUP BY customer_id",
    "SELECT name FROM products UNION SELECT name FROM categories",
    "SELECT o.order_id FROM orders o WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.order_id)",
])
def test_accepts_read_only(sql):
    assert validate(sql)


def test_adds_limit_when_missing():
    assert "LIMIT 50" in validate("SELECT * FROM orders", row_limit=50)


def test_keeps_existing_limit():
    out = validate("SELECT * FROM orders ORDER BY order_id LIMIT 5", row_limit=50)
    assert "LIMIT 5" in out and "LIMIT 50" not in out


def test_union_wrapped_with_limit():
    out = validate("SELECT name FROM products UNION SELECT name FROM categories", row_limit=10)
    assert out.endswith("LIMIT 10")
