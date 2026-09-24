"""CLI: ask a natural-language question, get back a SQL query.

Prerequisite: run main.py first to produce an (optionally AI-enriched) schema.json.

Examples
--------
# Generate SQL from the schema file only (no DB connection needed):
python ask.py --question "top 5 customers by total revenue last quarter"

# Connect read-only so the agent can validate/self-correct and run the query,
# printing the actual rows instead of just the SQL:
python ask.py \
  --question "monthly active renewals in 2024" \
  --conn "postgresql+psycopg://readonly:pw@host/salesdb"

# Multi-turn conversation (refine, correct, add/remove filters across turns):
python ask.py --interactive \
  --conn "postgresql+psycopg://readonly:pw@host/salesdb"
"""
from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

from dbagent.agent.domain_pack import load_domain
from dbagent.llm import LLM
from dbagent.agent.query_agent import QueryAgent
from dbagent.agent.query_models import Outcome, QueryResult
from dbagent.linking.schema_store import SchemaStore
from dbagent.execution.sql_runner import QueryExecutionError, QueryTimeoutError, SqlRunner


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Agentic natural-language to SQL")
    p.add_argument("--question", help="Question in plain English (omit with --interactive)")
    p.add_argument("--schema", default="schema.json", help="Path to enriched schema JSON")
    p.add_argument(
        "--conn",
        default=None,
        help="Optional read-only connection string; when set, the query is executed and rows are printed.",
    )
    p.add_argument(
        "--no-embed",
        action="store_true",
        help="Force keyword schema-linking instead of embeddings.",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="Start a multi-turn conversation (context is kept across questions).",
    )
    p.add_argument(
        "--domain",
        default=None,
        help="Domain pack name (e.g. 'warrants') or path, for per-application knowledge. Omit for generic.",
    )
    p.add_argument("--limit", type=int, default=20, help="Row cap when executing (--conn is set).")
    p.add_argument("--timeout", type=int, default=30, help="Statement timeout seconds when executing (--conn is set).")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    llm = LLM()
    store = SchemaStore.from_json(args.schema, llm=None if args.no_embed else llm)
    runner = SqlRunner(args.conn, timeout_seconds=args.timeout) if args.conn else None
    agent = QueryAgent(
        store, llm=llm, runner=runner, max_steps=args.max_steps, domain=load_domain(args.domain)
    )

    if args.interactive:
        _run_interactive(agent, runner, args)
        return

    if not args.question:
        raise SystemExit("Provide --question or use --interactive.")

    result = agent.ask(args.question)
    _print_result(result)
    _preview(result, runner, args.limit)


def _run_interactive(agent: QueryAgent, runner: SqlRunner | None, args) -> None:
    print("Conversation mode. Type a question, 'reset' to clear context, 'exit' to quit.\n")
    agent.reset()
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break
        if question.lower() == "reset":
            agent.reset()
            print("(context cleared)\n")
            continue

        result = agent.converse(question)
        _print_result(result)
        _preview(result, runner, args.limit)
        print()


def _preview(result: QueryResult, runner: SqlRunner | None, limit: int) -> None:
    """Execute the produced query and report the runtime outcome distinctly."""
    if result.status is not Outcome.SQL or not result.sql or runner is None:
        return
    try:
        execution = runner.execute(result.sql, limit=limit)
    except QueryTimeoutError as exc:
        print(f"\nTIMEOUT: query exceeded the time limit — partial results not shown.\n  {exc}")
        return
    except QueryExecutionError as exc:
        print(f"\nEXECUTION ERROR: {exc}")
        return

    if execution.row_count == 0:
        print("\nNO MATCHING RECORDS (the query ran successfully but returned 0 rows).")
        return
    print(f"\nSAMPLE ROWS ({execution.row_count}{'+' if execution.truncated else ''}):")
    for row in execution.rows:
        print(" ", row)
    if execution.truncated:
        print(f"  ... result truncated at {limit} rows (more exist; refine or export).")


def _print_result(result: QueryResult) -> None:
    line = "=" * 70
    print(line)
    print(f"QUESTION: {result.question}")
    print(f"DIALECT : {result.dialect}")
    print(f"OUTCOME : {result.status.value}")
    print(line)

    # Non-SQL outcomes carry a user-facing message instead of a query.
    if result.status is not Outcome.SQL:
        if result.message:
            print(f"\n{result.message}")
        if result.error:
            print(f"\nDETAIL: {result.error}")
        print()
        return

    print("\nSQL:\n")
    print(result.sql or "(no query produced)")
    if result.explanation:
        print("\nEXPLANATION:")
        print(f"  {result.explanation}")
    if result.criteria_applied:
        print("\nCRITERIA APPLIED:")
        for crit in result.criteria_applied:
            print(f"  - {crit}")
    if result.tables_used:
        print(f"\nTABLES USED: {', '.join(result.tables_used)}")
    if result.validation_error:
        print(f"\nVALIDATION: FAILED — {result.validation_error}")
    elif result.validated:
        print("\nVALIDATION: passed (EXPLAIN)")
    print()


if __name__ == "__main__":
    main()
