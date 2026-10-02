"""Evaluation harness: execution accuracy of the agent against a curated test suite.

Usage:
    python -m eval.run_eval                     # full suite, retries on
    python -m eval.run_eval --no-retry          # ablation: single attempt
    python -m eval.run_eval --difficulty hard --tags window,cte
    python -m eval.run_eval --ids easy_001,hard_003 -v
    python -m eval.run_eval --sleep 6           # pace requests on a free-tier quota

A prediction is correct when its result set equals the gold result set:
  * compared as multisets of rows (order-sensitive only when the case sets ordered: true)
  * column names/aliases and column order are ignored (each row is sorted by value)
  * numbers are rounded to 2 decimals; Decimal/float/int unified
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.agent import SQLAgent  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm import get_provider  # noqa: E402
from app.schema import get_schema  # noqa: E402

SUITE = Path(__file__).with_name("test_suite.yaml")
REPORTS = Path(__file__).with_name("reports")


def norm_value(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = round(float(v), 2)
        return 0.0 if f == 0 else f
    return str(v).strip()


def norm_rows(rows, ordered: bool):
    # Sort values inside each row so column order/aliases don't matter.
    out = [tuple(sorted((norm_value(v) for v in r), key=lambda x: (x is None, type(x).__name__, str(x))))
           for r in rows]
    return out if ordered else Counter(out)


def run_gold(conn, sql: str):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def load_cases(args) -> list[dict]:
    cases = yaml.safe_load(SUITE.read_text(encoding="utf-8"))["cases"]
    if args.difficulty:
        cases = [c for c in cases if c["difficulty"] in args.difficulty.split(",")]
    if args.tags:
        wanted = set(args.tags.split(","))
        cases = [c for c in cases if wanted & set(map(str, c.get("tags", [])))]
    if args.ids:
        ids = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in ids]
    return cases


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-retry", action="store_true", help="single attempt (ablation of self-correction)")
    p.add_argument("--difficulty", help="comma list: easy,medium,hard")
    p.add_argument("--tags", help="comma list of tags")
    p.add_argument("--ids", help="comma list of case ids")
    p.add_argument("--check-gold", action="store_true", help="only verify gold SQL runs; no LLM calls")
    p.add_argument("--sleep", type=float, default=0, help="seconds to wait between cases (free-tier rate limits)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    s = get_settings()
    cases = load_cases(args)
    if not cases:
        print("No cases selected.")
        return 1

    gold_conn = psycopg.connect(s.admin_database_url, autocommit=True)
    gold_conn.execute("SET default_transaction_read_only = on")

    if args.check_gold:
        bad = 0
        for c in cases:
            try:
                rows = run_gold(gold_conn, c["gold_sql"])
                flag = "  (EMPTY)" if not rows else ""
                print(f"ok   {c['id']:<12} {len(rows):>5} rows{flag}")
            except psycopg.Error as e:
                bad += 1
                print(f"FAIL {c['id']:<12} {str(e).splitlines()[0]}")
        return 1 if bad else 0

    retries = 1 if args.no_retry else s.max_retries
    agent = SQLAgent(get_provider(), get_schema(), max_retries=retries, summarize=False)

    results = []
    t_start = time.perf_counter()
    for i, c in enumerate(cases, 1):
        if i > 1 and args.sleep:
            time.sleep(args.sleep)  # pace requests under free-tier per-minute quotas
        gold = run_gold(gold_conn, c["gold_sql"])
        resp = agent.ask(c["question"])
        ordered = bool(c.get("ordered"))
        correct = resp.success and norm_rows(resp.rows, ordered) == norm_rows(gold, ordered)
        r = {
            "id": c["id"], "difficulty": c["difficulty"], "tags": c.get("tags", []),
            "question": c["question"], "correct": correct, "executed": resp.success,
            "attempts": len(resp.attempts), "first_try_executed": bool(resp.attempts) and resp.attempts[0].error is None,
            "latency_ms": resp.latency_ms, "pred_sql": resp.sql, "error": resp.error,
            "error_types": [a.error_type for a in resp.attempts if a.error_type],
            "gold_rows": len(gold), "pred_rows": resp.row_count,
        }
        results.append(r)
        mark = "PASS" if correct else ("WRONG" if resp.success else "FAIL")
        print(f"[{i:>2}/{len(cases)}] {mark:<5} {c['id']:<12} attempts={r['attempts']} {resp.latency_ms}ms")
        if args.verbose and not correct:
            print(f"      Q: {c['question']}\n      pred: {resp.sql}\n      err: {resp.error}")

    report = summarize(results, retries, time.perf_counter() - t_start)
    write_report(report, results)
    return 0


def summarize(results: list[dict], retries: int, elapsed: float) -> dict:
    n = len(results)
    by_diff = defaultdict(lambda: [0, 0])
    for r in results:
        by_diff[r["difficulty"]][0] += r["correct"]
        by_diff[r["difficulty"]][1] += 1
    recovered = [r for r in results if r["executed"] and not r["first_try_executed"]]
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "provider": get_settings().llm_provider,
        "model": get_settings().llm_model,
        "effort": get_settings().llm_effort,
        "max_retries": retries,
        "cases": n,
        "execution_accuracy": round(sum(r["correct"] for r in results) / n, 4),
        "valid_sql_rate": round(sum(r["executed"] for r in results) / n, 4),
        "first_try_valid_rate": round(sum(r["first_try_executed"] for r in results) / n, 4),
        "recovered_by_self_correction": len(recovered),
        "avg_attempts": round(sum(r["attempts"] for r in results) / n, 2),
        "avg_latency_ms": int(sum(r["latency_ms"] for r in results) / n),
        "by_difficulty": {k: {"correct": v[0], "total": v[1], "accuracy": round(v[0] / v[1], 4)}
                          for k, v in sorted(by_diff.items())},
        "elapsed_s": round(elapsed, 1),
    }


def write_report(report: dict, results: list[dict]) -> None:
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = "noretry" if report["max_retries"] == 1 else "retry"
    (REPORTS / f"{stamp}-{tag}.json").write_text(json.dumps({"summary": report, "results": results}, indent=2,
                                                            default=str), encoding="utf-8")
    md = [f"## Eval run {report['timestamp']} — {report['model']} (effort={report['effort']}, "
          f"max_retries={report['max_retries']})", "",
          "| Metric | Value |", "|---|---|",
          f"| Execution accuracy | **{report['execution_accuracy']:.1%}** ({report['cases']} cases) |",
          f"| Valid SQL rate | {report['valid_sql_rate']:.1%} |",
          f"| First-try valid SQL | {report['first_try_valid_rate']:.1%} |",
          f"| Recovered by self-correction | {report['recovered_by_self_correction']} |",
          f"| Avg attempts | {report['avg_attempts']} |",
          f"| Avg latency | {report['avg_latency_ms']} ms |", "",
          "| Difficulty | Accuracy |", "|---|---|"]
    md += [f"| {k} | {v['accuracy']:.1%} ({v['correct']}/{v['total']}) |" for k, v in report["by_difficulty"].items()]
    failures = [r for r in results if not r["correct"]]
    if failures:
        md += ["", "### Misses", ""] + [f"- `{r['id']}` — {'wrong result' if r['executed'] else r['error']}"
                                       for r in failures]
    (REPORTS / f"{stamp}-{tag}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md[:14]))
    print(f"\nReport written to eval/reports/{stamp}-{tag}.(json|md)")


if __name__ == "__main__":
    raise SystemExit(main())
