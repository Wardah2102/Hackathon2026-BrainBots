"""Accuracy evaluation harness for the NL->SQL agent.

Turns "is it accurate?" into numbers you can A/B against every prompt, retrieval
or domain-pack change. It scores three things that matter for a schema-grounded
agent, without needing a full labelled result set:

- outcome accuracy : does a request resolve to the right action (sql / clarify /
  refused / unavailable)?
- table recall     : do the expected real tables appear in the produced SQL
  (i.e. did schema linking surface the right join partners)?
- executable rate  : does the SQL actually run against the live DB (when
  DB_CONNECTION_STRING is set and a case is marked must_execute)?

Usage:
    python evaluate.py                       # uses eval_cases.json + .env config
    python evaluate.py --cases my_cases.json --domain warrants --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from dbagent.agent.domain_pack import load_domain
from dbagent.agent.query_agent import QueryAgent
from dbagent.linking.schema_store import SchemaStore
from dbagent.execution.sql_runner import QueryExecutionError, SqlRunner
from dbagent.llm import LLM


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate NL->SQL agent accuracy")
    p.add_argument("--cases", default="eval_cases.json", help="Path to the gold cases JSON.")
    p.add_argument("--schema", default=os.getenv("SCHEMA_PATH", "schema.json"))
    p.add_argument("--domain", default=os.getenv("DOMAIN_PACK") or None)
    p.add_argument("--conn", default=os.getenv("DB_CONNECTION_STRING") or None)
    p.add_argument("--no-embed", action="store_true", help="Force keyword schema-linking.")
    p.add_argument("--max-steps", type=int, default=int(os.getenv("AGENT_MAX_STEPS", "8")))
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def _tables_in_sql(sql: str, known: list[str]) -> set[str]:
    """Which known qualified tables are referenced in the SQL (case-insensitive)."""
    low = sql.lower()
    found = set()
    for name in known:
        # Word-boundary match so 'Rounds' doesn't match 'IssueRounds'.
        if re.search(rf"(?<![\w.]){re.escape(name.lower())}(?![\w])", low):
            found.add(name)
    return found


def main() -> None:
    load_dotenv()
    args = parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    llm = LLM()
    store = SchemaStore.from_json(args.schema, llm=None if args.no_embed else llm)
    if not args.no_embed:
        store.ensure_embeddings()
    runner = SqlRunner(args.conn) if args.conn else None
    domain = load_domain(args.domain)
    known_tables = [t.qualified_name for t in store.model.tables]

    outcome_hits = outcome_total = 0
    recall_sum = recall_total = 0
    exec_hits = exec_total = 0
    failures: list[str] = []

    for i, case in enumerate(cases, 1):
        question = case["question"]
        agent = QueryAgent(store, llm=llm, runner=runner, max_steps=args.max_steps, domain=domain)
        result = agent.ask(question)
        line = f"[{i:>2}] {result.status.value:<11} {question}"

        expected_status = case.get("expected_status")
        if expected_status:
            outcome_total += 1
            if result.status.value == expected_status:
                outcome_hits += 1
            else:
                line += f"  <- expected {expected_status}"
                failures.append(f"#{i} status {result.status.value} != {expected_status}: {question}")

        expected_tables = case.get("expected_tables")
        if expected_tables:
            recall_total += 1
            found = _tables_in_sql(result.sql, known_tables) | {
                t for t in result.tables_used if t in known_tables
            }
            hit = {t for t in expected_tables if t in found}
            recall = len(hit) / len(expected_tables)
            recall_sum += recall
            if recall < 1.0:
                missing = sorted(set(expected_tables) - hit)
                line += f"  missing tables: {missing}"
                failures.append(f"#{i} table recall {recall:.0%} (missing {missing}): {question}")

        if case.get("must_execute") and runner is not None and result.sql:
            exec_total += 1
            try:
                runner.execute(result.sql, limit=1)
                exec_hits += 1
            except QueryExecutionError as exc:
                line += "  <- EXEC FAILED"
                failures.append(f"#{i} exec failed ({exc}): {question}")

        print(line)
        if args.verbose and result.sql:
            print(f"      SQL: {result.sql.replace(chr(10), ' ')}")

    print("\n=== Summary ===")
    if outcome_total:
        print(f"Outcome accuracy : {outcome_hits}/{outcome_total} = {outcome_hits / outcome_total:.0%}")
    if recall_total:
        print(f"Table recall     : {recall_sum / recall_total:.0%} over {recall_total} case(s)")
    if exec_total:
        print(f"Executable rate  : {exec_hits}/{exec_total} = {exec_hits / exec_total:.0%}")
    if failures:
        print(f"\n{len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
