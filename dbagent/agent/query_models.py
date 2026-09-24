"""Typed models for the natural-language -> SQL agent."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    """How a turn ended, so callers can react without parsing prose.

    The agent may not always produce a query: a request can be ambiguous
    (clarify), destructive/out-of-scope (refused) or reference data the model
    does not expose (unavailable). Execution adds runtime outcomes (no_data,
    error, timeout). This lets the UI distinguish "no matching records" from a
    genuine failure (AC-20, AC-21, AC-22, AC-23).
    """

    SQL = "sql"                       # a read-only query was produced
    NEEDS_CLARIFICATION = "clarify"   # request is genuinely ambiguous
    REFUSED = "refused"               # write/destructive/out-of-scope request
    UNAVAILABLE = "unavailable"       # requested field/data not in the model
    NO_DATA = "no_data"               # query ran successfully, zero rows
    ERROR = "error"                   # query/service failure
    TIMEOUT = "timeout"               # query exceeded the execution limit


class AgentStep(BaseModel):
    """One reasoning/tool iteration, kept for transparency and debugging."""

    tool: str
    arguments: dict = Field(default_factory=dict)
    result_summary: str = ""


class QueryResult(BaseModel):
    question: str
    sql: str
    dialect: str
    status: Outcome = Outcome.SQL
    explanation: str = ""
    # User-facing text for non-SQL outcomes (clarifying question, refusal
    # reason, unavailability explanation, error message).
    message: str = ""
    tables_used: list[str] = Field(default_factory=list)
    # The user-facing columns to show, in order. Technical/internal columns the
    # user did not ask for (keys, ids, join helpers) are excluded so they stay
    # hidden from the result grid and export.
    display_columns: list[str] = Field(default_factory=list)
    # Human-readable list of the filters/criteria the query encodes (AC-24).
    criteria_applied: list[str] = Field(default_factory=list)
    validated: bool = False
    validation_error: str | None = None
    steps: list[AgentStep] = Field(default_factory=list)
    sample_rows: list[dict] = Field(default_factory=list)
    # Execution metadata (populated only when the query is actually run).
    row_count: int | None = None
    truncated: bool = False           # result was capped (AC-10 completeness)
    error: str | None = None          # execution error, distinct from validation
