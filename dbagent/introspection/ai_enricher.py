"""Uses Azure OpenAI to add human-readable business descriptions to the schema.

The raw catalog gives names and types; the LLM infers what each table/column
*means* in business terms (e.g. "ARR" = annual recurring revenue), which makes
downstream natural-language querying far more accurate.
"""
from __future__ import annotations

import json
import logging
import os

from openai import AzureOpenAI

from dbagent.introspection.schema_model import SchemaModel, TableModel

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a data analyst documenting a database schema. Given a table's "
    "structure and sample values, write concise business descriptions. "
    "Table and column names may be in any language (e.g. German, French, Dutch); "
    "interpret their meaning correctly and write the descriptions in English so "
    "they bridge languages for downstream search. Do not rename or translate the "
    "identifiers themselves. "
    "Respond ONLY with JSON of the form: "
    '{"table": "<one sentence>", "columns": {"<col>": "<short phrase>"}}.'
)


class AIEnricher:
    def __init__(
        self,
        deployment: str | None = None,
        client: AzureOpenAI | None = None,
    ):
        # Private Azure OpenAI keeps schema/sample data inside the enterprise boundary.
        self.client = client or AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"),
        )
        self.deployment = deployment or os.environ["AZURE_OPENAI_ENRICH_DEPLOYMENT"]

    def enrich(self, model: SchemaModel) -> SchemaModel:
        for table in model.tables:
            try:
                self._describe_table(table)
            except Exception as exc:  # keep going; descriptions are best-effort
                logger.warning("Enrichment failed for %s: %s", table.qualified_name, exc)
        return model

    def _describe_table(self, table: TableModel) -> None:
        response = self.client.chat.completions.create(
            model=self.deployment,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._build_context(table)},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")

        table.description = data.get("table")
        col_descriptions: dict[str, str] = data.get("columns", {})
        for column in table.columns:
            if column.name in col_descriptions:
                column.description = col_descriptions[column.name]

    @staticmethod
    def _build_context(table: TableModel) -> str:
        lines = [f"Table: {table.qualified_name}"]
        if table.row_count is not None:
            lines.append(f"Row count: {table.row_count}")
        lines.append("Columns:")
        for col in table.columns:
            flags = []
            if col.primary_key:
                flags.append("PK")
            if not col.nullable:
                flags.append("NOT NULL")
            samples = f" e.g. {col.sample_values}" if col.sample_values else ""
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  - {col.name} ({col.data_type}){flag_str}{samples}")
        if table.foreign_keys:
            lines.append("Foreign keys:")
            for fk in table.foreign_keys:
                target = f"{fk.referred_schema + '.' if fk.referred_schema else ''}{fk.referred_table}"
                lines.append(f"  - {fk.columns} -> {target}{fk.referred_columns}")
        return "\n".join(lines)
