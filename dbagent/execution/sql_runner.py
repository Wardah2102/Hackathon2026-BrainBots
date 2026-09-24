"""Optional read-only SQL validation and execution for agent self-correction.

Validation uses the database's own planner via ``EXPLAIN`` (no rows touched),
so the agent gets a real error message when a query references a wrong column or
join. Execution is strictly guarded: SELECT-only, capped with a LIMIT, and
bounded by a statement timeout so a runaway query cannot hang the request.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

logger = logging.getLogger(__name__)

_SELECT_ONLY = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|exec)\b",
    re.IGNORECASE,
)
# Substrings that identify a timeout/cancellation across common drivers.
_TIMEOUT_HINTS = (
    "timeout", "timed out", "canceling statement", "cancelled", "query_canceled",
    "statement_timeout", "lock request time out", "execution time",
)

# EXPLAIN prefix differs per dialect.
_EXPLAIN_PREFIX = {
    "postgresql": "EXPLAIN ",
    "mysql": "EXPLAIN ",
    "mssql": None,          # SQL Server uses SET SHOWPLAN; skip live validation.
    "ibm_db_sa": None,      # DB2 EXPLAIN writes to a plan table; skip.
    "sqlite": "EXPLAIN ",
}


class QueryExecutionError(RuntimeError):
    """The query failed to execute (bad SQL, DB error, service unavailable)."""


class QueryTimeoutError(QueryExecutionError):
    """The query exceeded the configured execution time limit (AC-22)."""


@dataclass
class ExecutionResult:
    """Outcome of running a query, so callers can tell empty from truncated."""

    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False          # more rows existed than the cap allowed


def is_read_only(sql: str) -> tuple[bool, str]:
    """Static guard: allow a single SELECT/WITH statement only.

    Module-level so the agent can enforce read-only even without a live DB
    connection (AC-04, AC-18, AC-19 — prompt/SQL-injection resistance).
    """
    if not sql or not _SELECT_ONLY.match(sql):
        return False, "Only SELECT/WITH statements are allowed."
    if _FORBIDDEN.search(sql):
        return False, "Statement contains a forbidden (write/DDL) keyword."
    if ";" in sql.strip().rstrip(";"):
        return False, "Multiple statements are not allowed."
    return True, ""


class SqlRunner:
    """Read-only gateway to the live database. Optional — the agent works without it."""

    def __init__(
        self,
        connection_string: str,
        timeout_seconds: int = 30,
        query_cost_limit: int = 0,
        pool_size: int = 5,
        max_overflow: int = 5,
    ):
        # Bounded pool so a burst of concurrent requests queues instead of opening
        # unlimited connections against the DB (OWASP A04 - resource exhaustion).
        self.engine: Engine = create_engine(
            connection_string, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
        )
        self.dialect = self.engine.dialect.name
        self.timeout_seconds = timeout_seconds
        # Mssql-only: SET QUERY_GOVERNOR_COST_LIMIT rejects a query up front if its
        # estimated plan cost exceeds this, before any work runs. 0 = disabled.
        self.query_cost_limit = query_cost_limit

    def is_read_only(self, sql: str) -> tuple[bool, str]:
        return is_read_only(sql)

    def supports_validation(self) -> bool:
        """True only when this dialect can be validated via EXPLAIN.

        For dialects where EXPLAIN isn't usable (SQL Server, DB2) ``validate``
        accepts everything, so exposing a validation step just costs the agent a
        wasted LLM round-trip.
        """
        return _EXPLAIN_PREFIX.get(self.dialect, "EXPLAIN ") is not None

    def validate(self, sql: str) -> tuple[bool, str]:
        """Return (ok, error). Uses EXPLAIN/SHOWPLAN where supported, else a syntax pre-check."""
        ok, reason = is_read_only(sql)
        if not ok:
            return False, reason

        if self.dialect == "mssql":
            return self._validate_mssql(sql)

        prefix = _EXPLAIN_PREFIX.get(self.dialect, "EXPLAIN ")
        if prefix is None:
            return True, ""  # can't safely EXPLAIN this dialect; accept as-is
        try:
            with self.engine.connect() as conn:
                self._apply_timeout(conn)
                conn.execute(text(prefix + sql.rstrip(";")))
            return True, ""
        except SQLAlchemyError as exc:
            return False, str(getattr(exc, "orig", exc))

    def _validate_mssql(self, sql: str) -> tuple[bool, str]:
        """SQL Server has no EXPLAIN; SET SHOWPLAN_XML makes the next statement return
        its estimated plan instead of executing it, so no rows are ever touched."""
        try:
            with self.engine.connect() as conn:
                self._apply_timeout(conn)
                conn.execute(text("SET SHOWPLAN_XML ON"))
                try:
                    conn.execute(text(sql.rstrip(";")))
                finally:
                    conn.execute(text("SET SHOWPLAN_XML OFF"))
            return True, ""
        except SQLAlchemyError as exc:
            return False, str(getattr(exc, "orig", exc))

    def execute(self, sql: str, limit: int = 20) -> ExecutionResult:
        """Run a read-only query, capped and time-bounded.

        Fetches one row beyond ``limit`` to detect (and flag) truncation without
        silently hiding it (AC-10). Raises ``QueryTimeoutError`` on timeout and
        ``QueryExecutionError`` on any other failure so callers never present a
        failure as a successful empty result (AC-20, AC-21, AC-22).
        """
        ok, reason = is_read_only(sql)
        if not ok:
            raise QueryExecutionError(reason)

        probe = limit + 1  # one extra row reveals whether more data existed
        capped = sql.rstrip(";")
        if not re.search(r"\blimit\b|\bfetch\s+first\b|\btop\b", capped, re.IGNORECASE):
            if self.dialect == "mssql":
                select_match = re.match(r"^(\s*SELECT\s+(?:DISTINCT\s+)?)", capped, re.IGNORECASE)
                if select_match:
                    capped = capped[: select_match.end()] + f"TOP {probe} " + capped[select_match.end() :]
                else:
                    capped = f"SELECT TOP {probe} * FROM ({capped}) AS _agent_preview"
            else:
                capped = f"SELECT * FROM ({capped}) AS _agent_preview LIMIT {probe}"

        try:
            with self.engine.connect() as conn:
                self._apply_timeout(conn)
                rows = conn.execute(text(capped)).mappings().all()
        except (OperationalError, DBAPIError, SQLAlchemyError) as exc:
            detail = str(getattr(exc, "orig", exc))
            if _looks_like_timeout(detail):
                raise QueryTimeoutError(detail) from exc
            raise QueryExecutionError(detail) from exc

        data = [dict(r) for r in rows]
        truncated = len(data) > limit
        if truncated:
            data = data[:limit]
        return ExecutionResult(rows=data, row_count=len(data), truncated=truncated)

    def run(self, sql: str, limit: int = 20) -> list[dict]:
        """Backward-compatible thin wrapper returning just the rows."""
        return self.execute(sql, limit=limit).rows

    def _apply_timeout(self, conn) -> None:
        """Best-effort per-statement timeout, applied per dialect."""
        secs = self.timeout_seconds
        if secs and secs > 0:
            try:
                if self.dialect == "postgresql":
                    conn.execute(text(f"SET statement_timeout = {int(secs * 1000)}"))
                elif self.dialect == "mysql":
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {int(secs * 1000)}"))
                elif self.dialect == "mssql":
                    # pyodbc honours a per-connection timeout in seconds.
                    raw = conn.connection.dbapi_connection
                    if raw is not None:
                        raw.timeout = int(secs)
            except Exception as exc:  # timeout is a safety net, never fatal to set
                logger.debug("Could not set statement timeout: %s", exc)

        if self.dialect == "mssql" and self.query_cost_limit > 0:
            try:
                # Rejects the query outright if its estimated plan cost exceeds this,
                # before any rows are touched (defends against expensive read-only SELECTs).
                conn.execute(text(f"SET QUERY_GOVERNOR_COST_LIMIT {int(self.query_cost_limit)}"))
            except Exception as exc:
                logger.debug("Could not set query governor cost limit: %s", exc)


def _looks_like_timeout(detail: str) -> bool:
    low = detail.lower()
    return any(h in low for h in _TIMEOUT_HINTS)
