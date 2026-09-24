"""CLI: read a database's structure and (optionally) enrich it with AI descriptions.

Examples
--------
# Whole database (all non-system schemas), no AI:
python main.py --conn "postgresql+psycopg://readonly:pw@host/salesdb" --no-ai

# Scope a shared DB2 instance to two application schemas, with AI descriptions:
python main.py \
  --conn "db2+ibm_db_sa://readonly:pw@host:50000/WAREHOUSE" \
  --schema RENEWALS --schema COMMERCIAL \
  --out schema.json
"""
from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

from dbagent.introspection.ai_enricher import AIEnricher
from dbagent.introspection.config import ScopeConfig
from dbagent.introspection.introspector import SchemaIntrospector
from dbagent.introspection.report import print_report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI-assisted database schema reader")
    p.add_argument("--conn", required=True, help="SQLAlchemy connection string")
    p.add_argument(
        "--schema",
        action="append",
        default=[],
        dest="schemas",
        help="Schema to include (repeatable). Omit for all non-system schemas.",
    )
    p.add_argument(
        "--include-table", action="append", default=[], dest="include_tables",
        help="Glob on SCHEMA.TABLE to include (repeatable).",
    )
    p.add_argument(
        "--exclude-table", action="append", default=[], dest="exclude_tables",
        help="Glob on SCHEMA.TABLE to exclude (repeatable).",
    )
    p.add_argument("--sample-rows", type=int, default=3)
    p.add_argument("--no-row-counts", action="store_true")
    p.add_argument("--no-ai", action="store_true", help="Skip AI enrichment")
    p.add_argument("--verbose", action="store_true", help="Verbose DEBUG logging")
    p.add_argument("--out", default="schema.json", help="Output JSON path")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    scope = ScopeConfig(
        include_schemas=args.schemas,
        include_tables=args.include_tables,
        exclude_tables=args.exclude_tables,
        sample_rows=args.sample_rows,
        include_row_counts=not args.no_row_counts,
    )

    model = SchemaIntrospector(args.conn, scope).introspect()

    if not args.no_ai:
        model = AIEnricher().enrich(model)

    print_report(model)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(model.model_dump_json(indent=2))
    print(f"\nWrote {len(model.tables)} tables to {args.out}")


if __name__ == "__main__":
    main()
