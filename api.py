"""FastAPI wrapper exposing the schema reader and NL->SQL agent over HTTP.

Run with Swagger UI:
    uvicorn api:app --reload
Then open http://localhost:8000/docs
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

import pyodbc
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from dbagent.introspection.ai_enricher import AIEnricher
from dbagent.introspection.config import ScopeConfig
from dbagent.agent.domain_pack import available_domains, load_domain
from dbagent.introspection.introspector import SchemaIntrospector
from dbagent.llm import LLM
from dbagent.agent.query_agent import QueryAgent
from dbagent.agent.query_models import Outcome, QueryResult
from dbagent.introspection.schema_model import SchemaModel
from dbagent.linking.schema_store import SchemaStore
from dbagent.execution.sql_runner import QueryExecutionError, QueryTimeoutError, SqlRunner

load_dotenv()
logging.basicConfig(level=logging.INFO)

# --- Server-side configuration -------------------------------------------
# Operational settings live here (driven by .env), not in request bodies, so the
# frontend only ever sends the question. Tune a deployment by editing .env.
SCHEMA_PATH = os.getenv("SCHEMA_PATH", "schema.json")
DB_CONNECTION = os.getenv("DB_CONNECTION_STRING") or None  # read-only; enables validation + preview
DEFAULT_DOMAIN = os.getenv("DOMAIN_PACK") or None          # e.g. "warrants"
USE_EMBEDDINGS = os.getenv("SCHEMA_LINKING", "embedding").strip().lower() != "keyword"
RUN_QUERIES = os.getenv("RUN_QUERIES", "true").strip().lower() == "true"
ROW_LIMIT = int(os.getenv("ROW_LIMIT", "2000"))
QUERY_TIMEOUT = int(os.getenv("QUERY_TIMEOUT_SECONDS", "30"))
# MSSQL-only: rejects a query up front if its estimated plan cost exceeds this. 0 disables it.
QUERY_GOVERNOR_COST_LIMIT = int(os.getenv("QUERY_GOVERNOR_COST_LIMIT", "300"))
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_POOL_MAX_OVERFLOW = int(os.getenv("DB_POOL_MAX_OVERFLOW", "5"))
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))

app = FastAPI(
    title="DB Schema AI",
    description="Introspect a database schema and turn natural-language questions into SQL.",
    version="1.0.0",
)

# Allow the Angular dev server (and any other configured origin) to call this
# API from the browser; without this, preflight OPTIONS requests get a 405.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:4200").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SchemaRequest(BaseModel):
    schemas: list[str] = Field(default_factory=list, description="Schemas to include (empty = all non-system).")
    include_tables: list[str] = Field(default_factory=list, description="Glob(s) on SCHEMA.TABLE to include.")
    exclude_tables: list[str] = Field(default_factory=list, description="Glob(s) on SCHEMA.TABLE to exclude.")
    sample_rows: int = Field(3, ge=0, description="Sample values learned per column (0 disables).")
    include_row_counts: bool = True
    no_ai: bool = Field(True, description="Skip AI enrichment (no Azure OpenAI needed).")
    out: str | None = Field("schema.json", description="If set, also persist the schema JSON to this path.")


class ConnectRequest(BaseModel):
    server: str = Field(..., description="SQL Server host, e.g. 'localhost', 'localhost\\SQLEXPRESS01' or 'myserver.database.windows.net'.")
    database: str = Field(..., description="Database name to connect to.")
    auth: Literal["windows", "sql"] = Field("windows", description="Windows (trusted) or SQL Server login.")
    username: str | None = Field(None, description="Required when auth='sql'.")
    password: str | None = Field(None, description="Required when auth='sql'.")

    @model_validator(mode="after")
    def _require_credentials_for_sql_auth(self) -> "ConnectRequest":
        if self.auth == "sql" and not (self.username and self.password):
            raise ValueError("username and password are required when auth='sql'.")
        return self


class ConnectResponse(BaseModel):
    database_id: str
    cached: bool
    dialect: str
    table_count: int


class AnalysisResponse(BaseModel):
    database_id: str
    dialect: str
    database: str | None = None
    table_count: int
    column_count: int
    relationship_count: int


class TestConnectionResponse(BaseModel):
    ok: bool
    dialect: str


class AskRequest(BaseModel):
    question: str = Field(..., description="Question in plain English (or any language).")
    domain: str | None = Field(
        None,
        description="Optional domain pack override (e.g. 'warrants'). Omit to use the server default.",
    )
    database_id: str | None = Field(
        None,
        description="Id returned by /connect. Omit to use the server's default DB_CONNECTION_STRING/SCHEMA_PATH.",
    )


class ConverseRequest(AskRequest):
    session_id: str | None = Field(
        None,
        description="Conversation id from a previous /converse call. Omit to start a new one.",
    )


class ConverseResponse(BaseModel):
    session_id: str
    result: QueryResult


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/domains", summary="List available domain packs")
def domains() -> dict[str, list[str]]:
    return {"domains": available_domains()}


# Per-database registry: maps a database_id (derived from the connection string)
# to its live connection + cached schema path. The connection string is kept
# only in memory (never written to disk) so credentials aren't persisted in
# plaintext; the introspected schema itself is cached on disk under
# SCHEMAS_DIR so repeat /connect calls for the same DB skip introspection.
_DATABASES: dict[str, dict] = {}
SCHEMAS_DIR = Path(os.getenv("SCHEMAS_DIR", "schemas"))


def _database_id(conn: str) -> str:
    return hashlib.sha256(conn.encode("utf-8")).hexdigest()[:16]


def _pick_odbc_driver() -> str:
    """Pick the newest installed 'ODBC Driver NN for SQL Server', if any."""
    candidates = [d for d in pyodbc.drivers() if "SQL Server" in d]
    numbered = sorted(
        (d for d in candidates if d.startswith("ODBC Driver")),
        key=lambda d: int(d.split()[2]),
        reverse=True,
    )
    if numbered:
        return numbered[0]
    if candidates:
        return candidates[0]
    raise HTTPException(
        status_code=503,
        detail="No SQL Server ODBC driver found on this machine. Install the Microsoft ODBC Driver for SQL Server.",
    )


def _build_mssql_conn(req: ConnectRequest) -> str:
    driver = quote_plus(_pick_odbc_driver())
    # ODBC Driver 18 defaults to Encrypt=yes and validates the server certificate;
    # trust it so connections to dev/hosted servers with self-signed certs succeed.
    extra = "&Encrypt=yes&TrustServerCertificate=yes"
    if req.auth == "windows":
        return f"mssql+pyodbc://@{req.server}/{req.database}?driver={driver}&trusted_connection=yes{extra}"
    user = quote_plus(req.username or "")
    pwd = quote_plus(req.password or "")
    return f"mssql+pyodbc://{user}:{pwd}@{req.server}/{req.database}?driver={driver}{extra}"


@app.post("/connect/test", response_model=TestConnectionResponse, summary="Verify database credentials without introspecting the schema")
def test_connection(req: ConnectRequest) -> TestConnectionResponse:
    """Quick connectivity check (opens a connection, runs SELECT 1). Unlike
    /connect, this never introspects the schema, so it stays fast regardless
    of how many tables the database has.
    """
    conn = _build_mssql_conn(req)
    try:
        runner = SqlRunner(conn, timeout_seconds=10, query_cost_limit=QUERY_GOVERNOR_COST_LIMIT)
        runner.execute("SELECT 1")
    except (QueryExecutionError, QueryTimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # driver/connection errors (bad server, bad creds, etc.)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TestConnectionResponse(ok=True, dialect=runner.dialect)


@app.post("/connect", response_model=ConnectResponse, summary="Register a database, caching its introspected schema")
def connect(req: ConnectRequest) -> ConnectResponse:
    """Give the agent a database. Introspects once and reuses the cached
    schema on subsequent calls for the same server/database/auth.
    """
    conn = _build_mssql_conn(req)
    database_id = _database_id(conn)
    schema_path = SCHEMAS_DIR / f"{database_id}.json"
    cached = schema_path.exists()

    if cached:
        model = SchemaModel.model_validate(json.loads(schema_path.read_text(encoding="utf-8")))
    else:
        scope = ScopeConfig(sample_rows=3, include_row_counts=True)
        try:
            model = SchemaIntrospector(conn, scope).introspect()
        except Exception as exc:  # surface driver/connection errors to the client
            raise HTTPException(status_code=400, detail=f"Introspection failed: {exc}") from exc
        SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    _DATABASES[database_id] = {"conn": conn, "schema_path": str(schema_path)}
    return ConnectResponse(
        database_id=database_id, cached=cached, dialect=model.dialect, table_count=len(model.tables)
    )


@app.get("/databases", summary="List databases registered this server run")
def list_databases() -> list[dict]:
    # Connection strings are intentionally omitted from the response.
    return [
        {"database_id": db_id, "schema_path": info["schema_path"]}
        for db_id, info in _DATABASES.items()
    ]


# database_id is a 16-char hex digest of the connection string; it ends up in a
# filename, so restrict it to that shape to prevent path traversal (OWASP A01/A03).
_DATABASE_ID_RE = re.compile(r"^[a-f0-9]{16}$")


@app.get(
    "/databases/{database_id}/analysis",
    response_model=AnalysisResponse,
    summary="Summarise the cached schema (tables, columns, relationships)",
)
def analyze_database(database_id: str) -> AnalysisResponse:
    """Report the schema knowledge prepared for a connected database. Reads the
    cached introspection produced by /connect; no live query touches business data.
    """
    if not _DATABASE_ID_RE.match(database_id):
        raise HTTPException(status_code=400, detail="Invalid database_id format.")
    schema_path = SCHEMAS_DIR / f"{database_id}.json"
    if not schema_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No analysis found for database_id={database_id}. Connect the database first.",
        )
    model = SchemaModel.model_validate(json.loads(schema_path.read_text(encoding="utf-8")))
    column_count = sum(len(table.columns) for table in model.tables)
    relationship_count = sum(len(table.foreign_keys) for table in model.tables)
    return AnalysisResponse(
        database_id=database_id,
        dialect=model.dialect,
        database=model.database,
        table_count=len(model.tables),
        column_count=column_count,
        relationship_count=relationship_count,
    )



@app.post("/schema", response_model=SchemaModel, summary="Introspect a database schema")
def read_schema(req: SchemaRequest) -> SchemaModel:
    if not DB_CONNECTION:
        raise HTTPException(
            status_code=503,
            detail="No database configured. Set DB_CONNECTION_STRING in .env.",
        )
    scope = ScopeConfig(
        include_schemas=req.schemas,
        include_tables=req.include_tables,
        exclude_tables=req.exclude_tables,
        sample_rows=req.sample_rows,
        include_row_counts=req.include_row_counts,
    )
    try:
        model = SchemaIntrospector(DB_CONNECTION, scope).introspect()
    except Exception as exc:  # surface driver/connection errors to the client
        raise HTTPException(status_code=400, detail=f"Introspection failed: {exc}") from exc

    if not req.no_ai:
        model = AIEnricher().enrich(model)

    if req.out:
        Path(req.out).write_text(model.model_dump_json(indent=2), encoding="utf-8")

    return model


@app.post("/ask", response_model=QueryResult, summary="Natural language to SQL")
def ask(req: AskRequest) -> QueryResult:
    _require_ready(req.database_id)
    started = time.perf_counter()
    agent, runner = _build_agent(req.domain, req.database_id)
    result = agent.ask(req.question)
    if RUN_QUERIES:
        _execute_into(result, runner, limit=ROW_LIMIT)
    logging.info("/ask handled in %.0f ms", (time.perf_counter() - started) * 1000)
    return result


# In-memory conversation store. Each session keeps a stateful QueryAgent so
# follow-up messages can refine the previous request (AC-14 .. AC-17). This is
# process-local; swap for a shared store if the API is scaled out.
_SESSIONS: dict[str, QueryAgent] = {}

# Conversations are also persisted to disk (one JSON file per session) so
# history survives a server restart and can be listed/reviewed later.
CONVERSATIONS_DIR = Path(os.getenv("CONVERSATIONS_DIR", "conversations"))
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# In-memory caches keep the /converse and /conversations hot paths off the disk:
#   _SESSION_RECORDS  full record per session, so a turn never re-reads the
#                     (growing) JSON file just to append to it.
#   _CONV_INDEX       lightweight summaries for the list view, so /conversations
#                     doesn't re-open and parse every file on each call.
_SESSION_RECORDS: dict[str, dict] = {}
_CONV_INDEX: dict[str, dict] | None = None
_CONV_INDEX_LOCK = threading.Lock()


def _validate_session_id(session_id: str) -> str:
    # session_id ends up in a filename; reject anything but a safe token to
    # prevent path traversal (OWASP A01/A03).
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format.")
    return session_id


def _session_file(session_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{_validate_session_id(session_id)}.json"


def _summarize_record(record: dict) -> dict:
    turns = record.get("turns", [])
    return {
        "session_id": record.get("session_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "turn_count": len(turns),
        "last_question": turns[-1]["question"] if turns else None,
    }


def _conversation_index() -> dict[str, dict]:
    """Lazily build (once) and return the session_id -> summary index."""
    global _CONV_INDEX
    if _CONV_INDEX is None:
        with _CONV_INDEX_LOCK:
            if _CONV_INDEX is None:
                index: dict[str, dict] = {}
                CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
                for path in CONVERSATIONS_DIR.glob("*.json"):
                    try:
                        record = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    index[record.get("session_id", path.stem)] = _summarize_record(record)
                _CONV_INDEX = index
    return _CONV_INDEX


def _load_session_record(session_id: str) -> dict | None:
    cached = _SESSION_RECORDS.get(session_id)
    if cached is not None:
        return cached
    path = _session_file(session_id)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    _SESSION_RECORDS[session_id] = record
    return record


def _persist_turn(session_id: str, question: str, result_json: dict, history: list[dict]) -> None:
    """Append a turn and flush the session to disk.

    Runs as a background task so the client isn't blocked on disk I/O, and works
    from in-memory state (record + a snapshot of the history) so it neither
    re-reads the file nor races with the next turn mutating the live history.
    """
    now = datetime.now(timezone.utc).isoformat()
    record = _load_session_record(session_id) or {
        "session_id": session_id,
        "created_at": now,
        "turns": [],
    }
    record["updated_at"] = now
    record["history"] = history
    record["turns"].append(
        {"timestamp": now, "question": question, "result": result_json}
    )
    _SESSION_RECORDS[session_id] = record
    _conversation_index()[session_id] = _summarize_record(record)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    _session_file(session_id).write_text(json.dumps(record, indent=2), encoding="utf-8")


@app.post("/converse", response_model=ConverseResponse, summary="Multi-turn NL to SQL")
def converse(req: ConverseRequest, background_tasks: BackgroundTasks) -> ConverseResponse:
    """Send a message within a conversation, keeping context across turns."""
    _require_ready(req.database_id)
    started = time.perf_counter()

    session_id = _validate_session_id(req.session_id) if req.session_id else None
    agent = _SESSIONS.get(session_id) if session_id else None
    if agent is None:
        agent, _ = _build_agent(req.domain, req.database_id)

        # Rehydrate a session that existed before a server restart, if any.
        record = _load_session_record(session_id) if session_id else None
        if record is not None:
            agent.load_history(record["history"])

        session_id = session_id or uuid.uuid4().hex
        _SESSIONS[session_id] = agent

    result = agent.converse(req.question)
    if RUN_QUERIES:
        _execute_into(result, agent.runner, limit=ROW_LIMIT)

    # Persist after the response is sent: snapshot the history/result now so the
    # background write is immune to the next turn mutating the live agent state.
    history_snapshot = list(agent.export_history())
    result_json = json.loads(result.model_dump_json())
    background_tasks.add_task(_persist_turn, session_id, req.question, result_json, history_snapshot)
    logging.info("/converse handled in %.0f ms (session %s)", (time.perf_counter() - started) * 1000, session_id)
    return ConverseResponse(session_id=session_id, result=result)


@app.delete("/converse/{session_id}", summary="End a conversation")
def end_conversation(session_id: str) -> dict[str, str]:
    _SESSIONS.pop(session_id, None)
    _SESSION_RECORDS.pop(session_id, None)
    _conversation_index().pop(session_id, None)
    path = _session_file(session_id)
    if path.exists():
        path.unlink()
    return {"status": "ended", "session_id": session_id}


@app.get("/conversations", summary="List persisted conversations")
def list_conversations() -> list[dict]:
    return list(_conversation_index().values())


@app.get("/conversations/{session_id}", summary="Get the full turn history for a conversation")
def get_conversation(session_id: str) -> dict:
    record = _load_session_record(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No conversation found for session_id={session_id}")
    return {
        "session_id": session_id,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "turns": record.get("turns", []),
    }


def _build_agent(domain: str | None, database_id: str | None = None) -> tuple[QueryAgent, SqlRunner | None]:
    """Construct an agent either from a registered /connect database or the
    server's default env-configured connection, honouring a domain override.

    Heavy, reusable dependencies (the Azure OpenAI client, the parsed schema plus
    its embedding index, and the SQL engine/connection pool) are cached in memory
    and shared across requests. Only the lightweight per-request agent wrapper is
    rebuilt each call, which is what keeps its short-lived conversation history
    isolated between requests.
    """
    llm = _get_llm()
    if database_id:
        entry = _DATABASES[database_id]  # presence already checked by _require_ready
        schema_path, conn = entry["schema_path"], entry["conn"]
    else:
        schema_path, conn = SCHEMA_PATH, DB_CONNECTION

    store = _get_schema_store(schema_path)
    runner = _get_runner(conn)
    agent = QueryAgent(
        store, llm=llm, runner=runner, max_steps=MAX_STEPS,
        domain=_load_domain(domain or DEFAULT_DOMAIN),
    )
    return agent, runner


# --- Shared, cached dependencies -----------------------------------------
# Building the Azure OpenAI client, parsing the schema (and, with embedding-based
# linking, embedding every table), and opening a SQL engine are all expensive.
# Previously this happened on every /ask and /converse call, which dominated
# request latency. We now build each once and reuse it across requests, guarded
# by locks so concurrent first-requests don't duplicate the work.
_LLM_SINGLETON: LLM | None = None
_LLM_LOCK = threading.Lock()

_SCHEMA_STORE_CACHE: dict[str, SchemaStore] = {}
_SCHEMA_STORE_LOCK = threading.Lock()

_RUNNER_CACHE: dict[str, SqlRunner] = {}
_RUNNER_LOCK = threading.Lock()


def _get_llm() -> LLM:
    """Return a process-wide Azure OpenAI client wrapper (thread-safe to share)."""
    global _LLM_SINGLETON
    if _LLM_SINGLETON is None:
        with _LLM_LOCK:
            if _LLM_SINGLETON is None:
                _LLM_SINGLETON = LLM()
    return _LLM_SINGLETON


def _get_schema_store(schema_path: str) -> SchemaStore:
    """Load and cache the schema store for ``schema_path``.

    The parsed schema and its embedding index are built once and kept in memory.
    Embeddings are warmed while the lock is held so the store is only published
    to the cache once fully ready — a concurrent request can never observe a
    half-built index, and the first real question doesn't pay the embedding cost.
    """
    key = str(schema_path)
    store = _SCHEMA_STORE_CACHE.get(key)
    if store is not None:
        return store
    with _SCHEMA_STORE_LOCK:
        store = _SCHEMA_STORE_CACHE.get(key)
        if store is None:
            llm = _get_llm() if USE_EMBEDDINGS else None
            store = SchemaStore.from_json(schema_path, llm=llm)
            if USE_EMBEDDINGS:
                try:
                    store.ensure_embeddings()
                except Exception as exc:  # keyword search still works without them
                    logging.warning("Embedding warm-up failed for %s (%s); "
                                    "will fall back to keyword search", key, exc)
            _SCHEMA_STORE_CACHE[key] = store
    return store


def _get_runner(conn: str | None) -> SqlRunner | None:
    """Return a cached SqlRunner (and its pooled SQL engine) for ``conn``."""
    if not conn:
        return None
    runner = _RUNNER_CACHE.get(conn)
    if runner is not None:
        return runner
    with _RUNNER_LOCK:
        runner = _RUNNER_CACHE.get(conn)
        if runner is None:
            runner = SqlRunner(
                conn,
                timeout_seconds=QUERY_TIMEOUT,
                query_cost_limit=QUERY_GOVERNOR_COST_LIMIT,
                pool_size=DB_POOL_SIZE,
                max_overflow=DB_POOL_MAX_OVERFLOW,
            )
            _RUNNER_CACHE[conn] = runner
    return runner


@app.on_event("startup")
def _warm_default_dependencies() -> None:
    """Warm the default schema store and SQL engine at boot, in parallel.

    Loading the schema (+ embeddings) and opening the SQL engine are independent
    and both do blocking I/O, so we run them concurrently. This is best-effort:
    any failure is logged and left for the first request to surface, so a bad
    default config never prevents the server from starting.
    """
    if not (SCHEMA_PATH and Path(SCHEMA_PATH).exists() and os.getenv("AZURE_OPENAI_API_KEY")):
        return

    tasks = [lambda: _get_schema_store(SCHEMA_PATH)]
    if DB_CONNECTION:
        tasks.append(lambda: _get_runner(DB_CONNECTION))

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(t) for t in tasks]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:  # non-fatal: first request will retry/report
                logging.warning("Startup warm-up task failed: %s", exc)




def _require_ready(database_id: str | None = None) -> None:
    if not os.getenv("AZURE_OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="Azure OpenAI is not configured (set AZURE_OPENAI_* in .env).")
    if database_id:
        if database_id not in _DATABASES:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown database_id={database_id}. Call POST /connect first.",
            )
        return
    if not Path(SCHEMA_PATH).exists():
        raise HTTPException(status_code=404, detail=f"Schema file not found: {SCHEMA_PATH}. Call /schema first.")
    if not os.getenv("AZURE_OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="Azure OpenAI is not configured (set AZURE_OPENAI_* in .env).")


def _load_domain(name: str | None):
    try:
        return load_domain(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _execute_into(result: QueryResult, runner: SqlRunner | None, limit: int) -> None:
    """Run the produced query and fold the runtime outcome into ``result``.

    Distinguishes 'no matching records' from a failure and never presents a
    partial/timed-out result as complete (AC-20, AC-21, AC-22, AC-10).
    """
    if result.status is not Outcome.SQL or not result.sql or runner is None:
        return
    exec_start = time.perf_counter()
    try:
        execution = runner.execute(result.sql, limit=limit)
    except QueryTimeoutError as exc:
        result.status = Outcome.TIMEOUT
        result.error = str(exc)
        result.message = "The query exceeded the time limit; partial results are not shown."
        return
    except QueryExecutionError as exc:
        result.status = Outcome.ERROR
        result.error = str(exc)
        result.message = "The query could not be executed against the database."
        return

    logging.info("SQL execution took %.0f ms (%d row(s))", (time.perf_counter() - exec_start) * 1000, execution.row_count)
    result.sample_rows = _shape_rows(execution.rows, result.display_columns)
    result.row_count = execution.row_count
    result.truncated = execution.truncated
    if execution.row_count == 0:
        result.status = Outcome.NO_DATA
        result.message = "No matching records were found for the requested criteria."


# A GUID/UUID value (optionally brace-wrapped) — an opaque technical identifier.
_GUID_RE = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)
# Column-name shapes that signal a true/false flag (is_/has_..., *_flag, *active).
_BOOL_NAME_PREFIXES = ("is", "has", "can", "was", "are", "allow", "use", "should")
_BOOL_NAME_KEYWORDS = {
    "flag", "bool", "boolean", "yn", "active", "inactive", "enabled", "disabled",
    "deleted", "blocked", "locked", "archived", "valid", "invalid", "verified",
    "confirmed", "approved", "rejected", "paid", "unpaid", "sent", "completed",
    "closed", "visible", "hidden", "default", "mandatory", "required", "readonly",
}


def _shape_rows(rows: list[dict], display_columns: list[str]) -> list[dict]:
    """Pick the user-facing columns and make the values human-readable.

    Two problems are handled here so raw, technical data never reaches the UI or
    the Excel export:
    - Column selection: use the agent's ``display_columns`` when given; otherwise
      drop columns that are clearly technical (opaque GUID keys, raw binary such
      as rowversion) even though the query still needs them.
    - Value formatting: render booleans as true/false rather than 0/1/2, trim the
      trailing padding SQL/DB2 CHAR columns carry, and stringify GUID/binary
      values so they serialize cleanly.
    """
    if not rows:
        return rows
    keep = _select_columns(rows, display_columns)
    by_column = {col: [row.get(col) for row in rows] for col in keep}
    bool_columns = {col for col in keep if _is_boolean_column(col, by_column[col])}
    return [
        {col: _format_value(row.get(col), col in bool_columns) for col in keep}
        for row in rows
    ]


def _select_columns(rows: list[dict], display_columns: list[str]) -> list[str]:
    """Return the ordered columns to show, hiding technical ones.

    Prefer the agent's explicit ``display_columns`` (case-insensitive match). With
    no guidance, keep every column except those whose values are entirely opaque
    identifiers (GUIDs) or raw binary, so a business user isn't shown key columns
    they never asked for. Never returns an empty list — a blank grid is worse.
    """
    all_columns = list(rows[0].keys())
    if display_columns:
        available = {col.lower(): col for col in all_columns}
        keep = [available[c.lower()] for c in display_columns if c.lower() in available]
        if keep:
            return keep
    by_column = {col: [row.get(col) for row in rows] for col in all_columns}
    kept = [
        col for col in all_columns
        if not _is_guid_column(by_column[col]) and not _is_binary_column(by_column[col])
    ]
    return kept or all_columns


def _looks_boolean_name(name: str) -> bool:
    tokens = [t for t in re.split(r"[_\s]+", name.lower()) if t]
    if tokens and tokens[0] in _BOOL_NAME_PREFIXES and len(tokens) > 1:
        return True
    if any(tok in _BOOL_NAME_KEYWORDS for tok in tokens):
        return True
    return bool(re.match(r"^(is|has|can|was|are|should)[A-Z]", name))


def _is_boolean_column(name: str, values: list) -> bool:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return False
    if all(isinstance(v, bool) for v in non_null):
        return True
    if not _looks_boolean_name(name):
        return False
    # A boolean-named column encoded as small ints: 0/1 or the legacy 1/2 scheme.
    return all(isinstance(v, int) and not isinstance(v, bool) and v in (0, 1, 2) for v in non_null)


def _is_guid_column(values: list) -> bool:
    non_null = [v for v in values if v is not None]
    return bool(non_null) and all(_is_guid(v) for v in non_null)


def _is_guid(value) -> bool:
    if isinstance(value, uuid.UUID):
        return True
    return isinstance(value, str) and bool(_GUID_RE.match(value))


def _is_binary_column(values: list) -> bool:
    non_null = [v for v in values if v is not None]
    return bool(non_null) and all(isinstance(v, (bytes, bytearray, memoryview)) for v in non_null)


def _format_value(value, is_bool: bool):
    if value is None:
        return None
    if is_bool:
        if isinstance(value, bool):
            return value
        if value in (0, 1, 2):
            return value == 1  # 1 -> true, 0/2 -> false
        return value
    if isinstance(value, str):
        return value.rstrip()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


