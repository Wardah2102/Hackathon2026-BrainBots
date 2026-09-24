"""Scope configuration: which slice of a (possibly huge, multi-app) database to read."""
from __future__ import annotations

from fnmatch import fnmatch

from pydantic import BaseModel, Field

# System schemas that are never business data and should always be skipped.
DEFAULT_SYSTEM_SCHEMAS = {
    "information_schema", "pg_catalog", "pg_toast",          # PostgreSQL
    "sys", "INFORMATION_SCHEMA", "guest", "db_owner",        # SQL Server
    "SYSIBM", "SYSCAT", "SYSSTAT", "SYSTOOLS", "SYSPROC",    # DB2
}


class ScopeConfig(BaseModel):
    """Defines the boundary of introspection for one target application.

    On a shared DB2 instance with many applications, set ``include_schemas`` to
    only the schemas that belong to your app/domain (e.g. RENEWALS, COMMERCIAL).
    """

    # Empty include_schemas means "all non-system schemas".
    include_schemas: list[str] = Field(default_factory=list)
    exclude_schemas: list[str] = Field(default_factory=list)

    # Glob patterns on the qualified name "SCHEMA.TABLE" (case-insensitive).
    include_tables: list[str] = Field(default_factory=list)
    exclude_tables: list[str] = Field(default_factory=list)

    sample_rows: int = 3        # sample values learned per column (0 disables)
    include_row_counts: bool = True

    def allows_schema(self, schema: str) -> bool:
        if schema in DEFAULT_SYSTEM_SCHEMAS or schema in self.exclude_schemas:
            return False
        if self.include_schemas:
            return schema in self.include_schemas
        return True

    def allows_table(self, schema: str, table: str) -> bool:
        qualified = f"{schema}.{table}".lower()
        if any(fnmatch(qualified, p.lower()) for p in self.exclude_tables):
            return False
        if self.include_tables:
            return any(fnmatch(qualified, p.lower()) for p in self.include_tables)
        return True
