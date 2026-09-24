"""Tool definitions and dispatch for the NL->SQL agent.

Tools are the agent's only way to touch the schema and database, which keeps the
model grounded in real metadata instead of hallucinating. The OpenAI tool
schemas below are passed to the model; ``ToolBox.dispatch`` executes the calls.
"""
from __future__ import annotations

import json
import logging

from dbagent.linking.schema_store import SchemaStore
from dbagent.execution.sql_runner import SqlRunner

logger = logging.getLogger(__name__)


def tool_specs(can_validate: bool) -> list[dict]:
    """OpenAI tool schemas. The validation tool appears only when the connected
    database can actually validate SQL (EXPLAIN-capable dialect)."""
    specs = [
        {
            "type": "function",
            "function": {
                "name": "search_tables",
                "description": (
                    "Find tables relevant to the question by keywords. Returns "
                    "qualified table names with short descriptions. Call this first. "
                    "The schema may be in a different language than the question, so "
                    "include keywords in the question's language AND their translations/"
                    "synonyms in the likely schema language and in English."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Business terms from the question plus cross-language "
                                "translations, e.g. ['client','customer','Kunde','revenue','chiffre d'affaires']."
                            ),
                        }
                    },
                    "required": ["keywords"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "describe_tables",
                "description": (
                    "Get full detail (columns, types, PKs, foreign keys, sample "
                    "values) for specific tables before writing SQL."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tables": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Qualified table names, e.g. ['sales.orders'].",
                        }
                    },
                    "required": ["tables"],
                },
            },
        },
    ]
    if can_validate:
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "validate_sql",
                    "description": (
                        "Validate a SELECT query against the live database using "
                        "EXPLAIN (no rows returned). Use before finishing to catch "
                        "wrong columns or joins, then fix and re-validate."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"sql": {"type": "string"}},
                        "required": ["sql"],
                    },
                },
            }
        )
    return specs


class ToolBox:
    def __init__(self, store: SchemaStore, runner: SqlRunner | None = None):
        self.store = store
        self.runner = runner

    def dispatch(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return "ERROR: arguments were not valid JSON."

        if name == "search_tables":
            matches = self.store.search(args.get("keywords", []))
            if not matches:
                return "No matching tables found. Try broader keywords."
            return self.store.compact_catalog_for(matches)
        if name == "describe_tables":
            return self.store.describe(args.get("tables", []))
        if name == "validate_sql" and self.runner is not None:
            ok, err = self.runner.validate(args.get("sql", ""))
            return "VALID" if ok else f"INVALID: {err}"
        return f"ERROR: unknown tool '{name}'."
