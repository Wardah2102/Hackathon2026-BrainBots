"""Reads a database's structure from just a connection string.

Uses SQLAlchemy's dialect-agnostic Inspector, which reads each engine's own
system catalog (information_schema / SYSCAT / sys.*). The same code therefore
works across PostgreSQL, SQL Server, DB2, and more.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from dbagent.introspection.config import ScopeConfig
from dbagent.introspection.schema_model import (
    ColumnModel,
    ForeignKeyModel,
    SchemaModel,
    TableModel,
)

logger = logging.getLogger(__name__)


class SchemaIntrospector:
    def __init__(self, connection_string: str, scope: ScopeConfig | None = None):
        # Read-only intent; the DB account itself should also be SELECT-only.
        self.engine: Engine = create_engine(connection_string, pool_pre_ping=True)
        self.scope = scope or ScopeConfig()

    def introspect(self) -> SchemaModel:
        inspector = inspect(self.engine)
        model = SchemaModel(
            dialect=self.engine.dialect.name,
            database=self.engine.url.database,
        )
        logger.info("Connected to %s (dialect=%s)", model.database, model.dialect)

        schemas = self._discover_schemas(inspector)
        logger.info("Schemas in scope: %s", schemas)

        for schema in schemas:
            table_names = inspector.get_table_names(schema=schema)
            logger.debug("Schema %s has %d table(s)", schema, len(table_names))
            for table_name in table_names:
                if not self.scope.allows_table(schema, table_name):
                    logger.debug("Skipping filtered table %s.%s", schema, table_name)
                    continue
                try:
                    table = self._introspect_table(inspector, schema, table_name)
                    model.tables.append(table)
                    logger.info(
                        "Read %s: %d column(s), %d FK(s)",
                        table.qualified_name,
                        len(table.columns),
                        len(table.foreign_keys),
                    )
                except SQLAlchemyError:
                    logger.warning("Skipped %s.%s (read error)", schema, table_name)

        logger.info("Introspected %d tables total", len(model.tables))
        return model

    def _discover_schemas(self, inspector) -> list[str]:
        all_schemas = inspector.get_schema_names()
        return [s for s in all_schemas if self.scope.allows_schema(s)]

    def _introspect_table(self, inspector, schema: str, name: str) -> TableModel:
        pk = inspector.get_pk_constraint(name, schema=schema).get(
            "constrained_columns", []
        ) or []

        columns: list[ColumnModel] = []
        for col in inspector.get_columns(name, schema=schema):
            columns.append(
                ColumnModel(
                    name=col["name"],
                    data_type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                    primary_key=col["name"] in pk,
                    default=None if col.get("default") is None else str(col["default"]),
                )
            )

        fks = [
            ForeignKeyModel(
                columns=fk.get("constrained_columns", []),
                referred_schema=fk.get("referred_schema"),
                referred_table=fk.get("referred_table", ""),
                referred_columns=fk.get("referred_columns", []),
            )
            for fk in inspector.get_foreign_keys(name, schema=schema)
        ]

        indexes = [
            idx["name"]
            for idx in inspector.get_indexes(name, schema=schema)
            if idx.get("name")
        ]

        table = TableModel(
            schema_name=schema,
            name=name,
            columns=columns,
            primary_key=pk,
            foreign_keys=fks,
            indexes=indexes,
        )

        if self.scope.sample_rows > 0:
            self._add_samples(table)
        if self.scope.include_row_counts:
            table.row_count = self._row_count(schema, name)
        return table

    def _add_samples(self, table: TableModel) -> None:
        # Learn real value patterns (e.g. a status column holds Active/Churned).
        from sqlalchemy import MetaData, Table

        try:
            meta = MetaData()
            reflected = Table(
                table.name, meta, schema=table.schema_name, autoload_with=self.engine
            )
            stmt = select(reflected).limit(self.scope.sample_rows)
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
        except SQLAlchemyError:
            return

        by_column: dict[str, list[str]] = {c.name: [] for c in table.columns}
        for row in rows:
            for col_name, value in row.items():
                if value is not None and col_name in by_column:
                    by_column[col_name].append(str(value)[:60])
        for column in table.columns:
            column.sample_values = by_column.get(column.name, [])

    def _row_count(self, schema: str, name: str) -> int | None:
        try:
            with self.engine.connect() as conn:
                # Identifiers come from the DB catalog, not user input.
                return conn.execute(
                    text(f'SELECT COUNT(*) FROM "{schema}"."{name}"')
                ).scalar_one()
        except SQLAlchemyError:
            return None
