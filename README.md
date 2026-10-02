# Text-to-SQL Analytics Agent

**Python · FastAPI · PostgreSQL · LLM APIs (Google Gemini or Anthropic Claude)**

An AI agent that turns natural-language business questions into SQL against a multi-table e-commerce database, runs the SQL safely, and explains the results in plain English. When a query fails, the agent reads the error, fixes the query, and retries on its own. A benchmark harness measures its accuracy against a curated test suite.

```
 question ──► schema-aware prompt ──► LLM (structured JSON: reasoning + sql)
                                              │
                    ┌─────────────────────────┘
                    ▼
            SQL guard (sqlglot) ──reject──┐
                    │ ok                  │
                    ▼                     │  error classified + schema hint
     Postgres (read-only role, READ ONLY  │  fed back into the next attempt
     txn, statement_timeout) ──error──────┤  (up to MAX_RETRIES)
                    │ rows                │
                    ▼                     │
        LLM summary of results      ◄─────┘ (on success)
```

## Features

- **Natural-language → SQL** over 9 related tables (customers, addresses, a category tree, products, orders, order_items, payments, shipments, reviews), plus **automatic plain-English summaries** of the results.
- **Self-correction loop**: guard rejections and Postgres errors (undefined column or table, grouping errors, type mismatches, timeouts and others) are sorted into error types. Each one gets a schema-derived hint, such as *"Did you mean `order_items.unit_price`?"*, and is sent back to the model for the next attempt. The UI shows the full attempt trace.
- **Several layers of read-only safety**:
  1. The agent connects only as `analyst_ro`, a role with `SELECT` grants only, `default_transaction_read_only = on`, and a 5s `statement_timeout`.
  2. Every query runs inside `BEGIN READ ONLY` and is always rolled back.
  3. `sqlglot` checks every query before it runs. It allows only a single SELECT/WITH statement and blocks DML/DDL (including data-modifying CTEs and `SELECT INTO`), dangerous functions (`pg_sleep`, `pg_read_file`, `dblink` and others), and system catalogs. It also adds a row `LIMIT`.
- **Schema-aware prompting**: the schema is read live from Postgres, including `COMMENT ON` descriptions, foreign keys, and sample values of enum-like columns. It sits in a cached system prompt alongside the business rules (revenue definition, category tree traversal) and a few worked examples.
- **Evaluation harness**: 40 curated questions (easy, medium and hard: joins, CTEs, window functions, cohorts) with gold SQL. Scoring is by **execution accuracy**, comparing result sets regardless of column order. A retry vs. no-retry ablation shows how much self-correction adds.

## Quick start

Requires Docker and Python 3.11+.

```bash
docker compose up -d                      # Postgres 16 + schema + seed data + read-only role
python -m venv .venv
.venv\Scripts\activate                    # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                    # then set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)
uvicorn app.main:app --reload
```

Open http://localhost:8000.

### Choosing an LLM

The agent works through a small `LLMProvider` interface (`app/llm.py`), and `LLM_PROVIDER` selects the implementation:

| `LLM_PROVIDER` | Key | Default model | Notes |
|---|---|---|---|
| `gemini` (default) | `GEMINI_API_KEY` | `gemini-3.5-flash` | Free tier available. Rate-limit responses (429) are retried with exponential backoff. If you keep hitting the free quota, set `LLM_MODEL=gemini-3.1-flash-lite`. |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | Paid. Uses a cached system prompt and structured outputs. |

Both providers return structured JSON (`{reasoning, sql}`) that is checked against a schema, so the SQL is never extracted from free text with a regex.

To regenerate the dataset (deterministic, seed=42): `python db/generate_seed.py`, then `docker compose down -v && docker compose up -d`.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/query` | `{"question": "..."}` → SQL, columns, rows, summary, attempt trace |
| `GET` | `/api/schema` | Introspected schema with descriptions |
| `GET` | `/api/examples` | Example questions |
| `GET` | `/health` | DB connectivity |

Interactive docs are at `/docs`.

## Evaluation

```bash
python -m eval.run_eval --check-gold     # sanity-check the gold SQL against the DB (no LLM calls)
python -m eval.run_eval                  # full suite with self-correction
python -m eval.run_eval --no-retry       # ablation: single attempt
python -m eval.run_eval --difficulty hard -v
python -m eval.run_eval --sleep 6        # pace requests to stay under a free-tier per-minute quota
```

Reports are written to `eval/reports/` as JSON plus a Markdown table. Metrics: execution accuracy (overall and per difficulty), valid-SQL rate, first-try valid rate, queries recovered by self-correction, average attempts, and latency.

### Results

_Run the harness and paste the Markdown table from `eval/reports/*.md` here._

## Tests

```bash
pytest -q
```

- **Guard tests**: unsafe SQL is rejected and valid analytics SQL is accepted.
- **Agent-loop tests**: a fake LLM and fake DB check recovery, retry limits, error hints, and unanswerable questions.

## Project layout

```
app/        FastAPI app, agent loop, LLM provider, SQL guard, schema introspection, read-only DB access
db/         schema DDL, deterministic seed generator + generated seed, read-only role
eval/       test_suite.yaml (40 cases) + run_eval.py
static/     single-page UI (results table, SQL, summary, self-correction trace, schema explorer)
tests/      pytest suite
```
