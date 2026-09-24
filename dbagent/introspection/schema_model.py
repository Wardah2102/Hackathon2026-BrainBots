"""Typed data models describing an introspected database schema."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnModel(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False
    default: str | None = None
    sample_values: list[str] = Field(default_factory=list)
    description: str | None = None  # filled in by the AI enricher


class ForeignKeyModel(BaseModel):
    columns: list[str]
    referred_schema: str | None = None
    referred_table: str = ""
    referred_columns: list[str] = Field(default_factory=list)


class TableModel(BaseModel):
    schema_name: str
    name: str
    row_count: int | None = None
    columns: list[ColumnModel] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyModel] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    description: str | None = None  # filled in by the AI enricher

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"


class SchemaModel(BaseModel):
    dialect: str
    database: str | None = None
    tables: list[TableModel] = Field(default_factory=list)
