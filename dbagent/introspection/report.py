"""Human-readable console report of an introspected schema.

Prints every table, its columns, and — importantly — the foreign-key
relationships, so you can verify the tool understood the database structure
without needing any AI.
"""
from __future__ import annotations

import logging

from dbagent.introspection.schema_model import SchemaModel

logger = logging.getLogger(__name__)


def print_report(model: SchemaModel) -> None:
    line = "=" * 70
    print(line)
    print(f"DATABASE: {model.database or '(unknown)'}   DIALECT: {model.dialect}")
    print(f"TABLES DISCOVERED: {len(model.tables)}")
    print(line)

    for table in model.tables:
        rows = "" if table.row_count is None else f"  (~{table.row_count} rows)"
        print(f"\n■ {table.qualified_name}{rows}")

        for col in table.columns:
            marks = []
            if col.primary_key:
                marks.append("PK")
            if not col.nullable:
                marks.append("NOT NULL")
            mark_str = f"  [{', '.join(marks)}]" if marks else ""
            samples = f"   e.g. {col.sample_values}" if col.sample_values else ""
            print(f"    - {col.name}: {col.data_type}{mark_str}{samples}")

        if table.indexes:
            print(f"    indexes: {', '.join(table.indexes)}")

        if table.foreign_keys:
            print("    relationships:")
            for fk in table.foreign_keys:
                target_schema = f"{fk.referred_schema}." if fk.referred_schema else ""
                print(
                    f"      {table.name}({', '.join(fk.columns)})"
                    f" -> {target_schema}{fk.referred_table}"
                    f"({', '.join(fk.referred_columns)})"
                )

    _print_relationship_summary(model)


def _print_relationship_summary(model: SchemaModel) -> None:
    edges = [
        (t.name, fk.referred_table)
        for t in model.tables
        for fk in t.foreign_keys
        if fk.referred_table
    ]
    print("\n" + "-" * 70)
    print(f"RELATIONSHIP SUMMARY: {len(edges)} foreign-key link(s)")
    print("-" * 70)
    for source, target in edges:
        print(f"  {source}  ──▶  {target}")
    if not edges:
        print("  (no foreign keys found — relationships may be implicit only)")
