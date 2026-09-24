# DB Schema AI — pluggable natural-language → SQL agent

Turn plain-language questions ("give me choice-round customers in Q4 2025,
excluding FIP") into a correct, **read-only** SQL query for *any* application's
database. The agent explores the real schema through tools so it never
hallucinates table or column names, holds a multi-turn conversation, asks for
clarification when a request is ambiguous, refuses write/destructive requests,
and reports errors/timeouts/empty-results distinctly.

Business knowledge (glossary, rules, example queries) lives in **domain packs**
so the same engine can serve Warrants today and another application tomorrow —
by swapping a JSON pack, not the code.

---

## How it works

```
                ┌───────────────┐   introspect    ┌──────────────┐
   database ───▶│ introspection │ ──────────────▶ │  schema.json │
                └───────────────┘                 └──────┬───────┘
                                                         │ load
      question ──▶ ┌────────────────────────────────────▼──────────────┐
                   │                 QueryAgent (agent)                 │
   domain pack ──▶ │  1. search_tables  → linking (keyword/embedding)   │
                   │  2. describe_tables → columns, keys, samples       │
                   │  3. draft SQL       → read-only SELECT             │
                   │  4. validate_sql    → EXPLAIN (execution, opt.)    │
                   └───────────────┬────────────────────┬──────────────┘
                                   │ QueryResult         │ optional run
                                   ▼                     ▼
                         status + SQL + criteria    execution (capped,
                         (or clarify/refuse/...)    time-bounded, safe)
```

1. **Introspection** reads the database structure into `schema.json` (optionally
   AI-enriched with column/table descriptions).
2. **Schema linking** picks only the tables relevant to a question (keyword or
   embedding search) so the model's context stays small.
3. **The agent** runs a tool-calling loop: search → describe → draft SQL →
   validate → self-correct, then returns a structured `QueryResult`.
4. **Execution** (optional) runs the final query read-only, capped with a row
   limit and a statement timeout.

Every turn ends with an **outcome status** so callers can react without parsing
prose:

| status        | meaning                                             |
|---------------|-----------------------------------------------------|
| `sql`         | a read-only query was produced                      |
| `clarify`     | request is genuinely ambiguous — agent asks         |
| `refused`     | write/destructive/out-of-scope request              |
| `unavailable` | requested field/data is not in the model            |
| `no_data`     | query ran successfully, zero matching rows          |
| `error`       | query/service failure                               |
| `timeout`     | query exceeded the execution limit                  |

---

## Project structure

```
main.py            entry point: introspect a DB → schema.json
ask.py             entry point: CLI (single question or --interactive)
api.py             entry point: FastAPI app (uvicorn api:app)
schema.json        default schema file (produced by main.py / /schema)
requirements.txt
dbagent/                       reusable library package
├── llm.py                     model-agnostic Azure OpenAI layer
├── introspection/            read DB structure + AI enrichment
├── linking/                  schema linking (pick relevant tables)
├── agent/                    NL→SQL agent, models, domain packs
├── execution/                read-only SQL validation/execution
└── domains/                  per-application knowledge packs (warrants.json)
```

---

## Setup

### 1. Prerequisites
- Python 3.10+
- An Azure OpenAI (Azure AI Foundry) resource with a chat deployment (and an
  embedding deployment if you want embedding-based schema linking)
- A read-only connection to the target database

### 2. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Install only the database driver(s) you need (already listed in
`requirements.txt`):

| Database    | Driver package    | Connection string example                                            |
|-------------|-------------------|----------------------------------------------------------------------|
| PostgreSQL  | `psycopg[binary]` | `postgresql+psycopg://readonly:pw@host/salesdb`                      |
| SQL Server  | `pyodbc`          | `mssql+pyodbc://user:pw@host/db?driver=ODBC+Driver+18+for+SQL+Server` |
| DB2         | `ibm-db-sa`       | `db2+ibm_db_sa://readonly:pw@host:50000/WAREHOUSE`                   |

### 3. Configure Azure OpenAI

Copy `.env.example` to `.env` and fill in your values:

```ini
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_API_VERSION=2024-10-21

# Optional per-role deployment overrides (sensible defaults are used otherwise):
AZURE_OPENAI_SQL_DEPLOYMENT=gpt-5                     # NL→SQL agent
AZURE_OPENAI_ENRICH_DEPLOYMENT=gpt-5-mini            # schema descriptions
AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-large # schema linking

# Operational settings for the API (the frontend never sends these):
SCHEMA_PATH=schema.json                # schema file the API serves
DB_CONNECTION_STRING=                   # read-only conn; enables validation + row preview
DOMAIN_PACK=warrants                    # default domain pack (omit for generic)
SCHEMA_LINKING=embedding                # 'embedding' (default) or 'keyword'
RUN_QUERIES=true                        # execute the final query and return sample rows
ROW_LIMIT=20                            # max rows returned per query
QUERY_TIMEOUT_SECONDS=30                # statement timeout (0 disables)
AGENT_MAX_STEPS=8                       # tool-loop budget

# Optional: where to look for domain packs (defaults to dbagent/domains)
# DOMAIN_PACK_DIR=C:\path\to\domains
```

Only the endpoint and key are strictly required. Swap any model by changing the
matching `*_DEPLOYMENT` variable — no code changes.

### 4. Produce the schema file

Point the tool at your database once to generate `schema.json`:

```powershell
# Whole database, no AI enrichment:
python main.py --conn "postgresql+psycopg://readonly:pw@host/salesdb" --no-ai

# Scope a shared instance to specific schemas, with AI descriptions:
python main.py --conn "db2+ibm_db_sa://readonly:pw@host:50000/WAREHOUSE" `
  --schema RENEWALS --schema COMMERCIAL --out schema.json
```

You can also generate it over HTTP via the `POST /schema` endpoint (below).

---

## Running the API

Start the server (Swagger UI is enabled):

```powershell
uvicorn api:app --reload
```

Then open **http://localhost:8000/docs** for interactive documentation.

### Endpoints

| Method & path           | Purpose                                                        |
|-------------------------|----------------------------------------------------------------|
| `GET /health`           | Liveness check → `{"status": "ok"}`                            |
| `GET /domains`          | List available domain packs                                    |
| `POST /schema`          | Introspect a database and (optionally) persist `schema.json`   |
| `POST /ask`             | One-shot natural-language → SQL                                |
| `POST /converse`        | Multi-turn conversation (keeps context across messages)        |
| `DELETE /converse/{id}` | End a conversation and free its context                        |

> `/ask` and `/converse` require `schema.json` to exist and
> `AZURE_OPENAI_API_KEY` to be set, otherwise they return `404`/`503`.

> **Frontend keeps it simple.** Operational settings (connection string, row
> limit, timeout, schema-linking mode, whether to execute, agent budget) are
> read from `.env` on the server — see the config block above. The frontend only
> ever sends the `question` (plus an optional `domain` / `session_id`).

### `POST /schema` — introspect a database

Request body:

| field                | type       | default        | notes                                             |
|----------------------|------------|----------------|---------------------------------------------------|
| `schemas`            | string[]   | `[]`           | Schemas to include (empty = all non-system)       |
| `include_tables`     | string[]   | `[]`           | Glob(s) on `SCHEMA.TABLE` to include              |
| `exclude_tables`     | string[]   | `[]`           | Glob(s) on `SCHEMA.TABLE` to exclude              |
| `sample_rows`        | int        | `3`            | Sample values learned per column (0 disables)     |
| `include_row_counts` | bool       | `true`         |                                                   |
| `no_ai`              | bool       | `true`         | Skip AI enrichment (no Azure OpenAI needed)       |
| `out`                | string     | `schema.json`  | If set, also persist the schema JSON to this path |

Returns the introspected `SchemaModel` (tables, columns, keys, samples).

### `POST /ask` — natural language → SQL

Request body (only the question is required):

| field      | type    | default      | notes                                                        |
|------------|---------|--------------|--------------------------------------------------------------|
| `question` | string  | *(required)* | Question in plain English (or any language)                  |
| `domain`   | string? | server default (`DOMAIN_PACK`) | Optional domain-pack override (e.g. `warrants`) |

Everything else (connection, execution, row limit, timeout, schema-linking mode,
agent budget) comes from server `.env` config — see the config block above.

Example:

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{ "question": "customers who organised a choice round in Q4 2025, excluding FIP" }'
```

Response (`QueryResult`):

```jsonc
{
  "question": "...",
  "sql": "SELECT DISTINCT c.Id, c.Name FROM ...",
  "dialect": "postgresql",
  "status": "sql",                     // see the outcome table above
  "explanation": "why this answers the question",
  "message": "",                       // filled for clarify/refuse/... outcomes
  "tables_used": ["dbo.Customers", "dbo.Rounds"],
  "criteria_applied": ["Round type = Choice", "Q4 2025", "FIP excluded"],
  "validated": true,                   // EXPLAIN passed (when conn provided)
  "validation_error": null,
  "row_count": 12,                     // when run=true
  "truncated": false,                  // true if capped at `limit`
  "sample_rows": [ { "...": "..." } ], // when run=true
  "error": null,
  "steps": [ { "tool": "search_tables", "...": "..." } ]
}
```

### `POST /converse` — multi-turn

Body is just `question` plus an optional `session_id` (and optional `domain`).
Omit `session_id` on the first call; the response returns one to send back on
follow-ups so context (previous filters, corrections, pronouns like "exclude
them") is preserved.

```bash
# Turn 1 — omit session_id to start a conversation
curl -X POST http://localhost:8000/converse -H "Content-Type: application/json" \
  -d '{"question": "sales-round customers in 2025"}'
# → { "session_id": "abc123...", "result": { ... } }

# Turn 2 — reuse the returned session_id to refine
curl -X POST http://localhost:8000/converse -H "Content-Type: application/json" \
  -d '{"session_id": "abc123...", "question": "only SME clients"}'

# End the conversation
curl -X DELETE http://localhost:8000/converse/abc123...
```

> Conversations are held in-process. If you run multiple API workers, put a
> shared store behind `_SESSIONS` or use sticky sessions.

---

## Using the CLI

The same engine is available without the HTTP layer.

```powershell
# Generate SQL from schema.json only (no DB connection needed):
python ask.py --question "top 5 customers by total warrants in 2025" --domain warrants

# Connect read-only so the agent can validate/self-correct and preview rows:
python ask.py --question "choice-round customers in Q4 2025, excluding FIP" `
  --conn "postgresql+psycopg://readonly:pw@host/salesdb" --run --domain warrants

# Multi-turn conversation (type 'reset' to clear context, 'exit' to quit):
python ask.py --interactive --domain warrants `
  --conn "postgresql+psycopg://readonly:pw@host/salesdb" --run
```

Key flags: `--schema` (schema path), `--domain`, `--conn`, `--run`, `--limit`,
`--timeout`, `--no-embed`, `--max-steps`, `--interactive`, `--verbose`.

---

## Domain packs (per-application knowledge)

A domain pack is a JSON file under `dbagent/domains/` (or `DOMAIN_PACK_DIR`) that
injects business knowledge into the agent:

- `glossary` — map business terms to the model (e.g. *FIP*, *warrants*, *choice
  round*)
- `rules` — domain guidance (quarter-on-round-date, FIP exclusion, aggregation
  level, …)
- `examples` — illustrative NL→SQL patterns to adapt

Select one with `--domain <name>` (CLI) or `"domain": "<name>"` (API). Omit it to
run fully generic. To support a new application, drop a new
`dbagent/domains/<app>.json` — no code changes.

`GET /domains` lists the packs it can find.

---

## Safety & guarantees

- **Read-only**: only a single `SELECT`/`WITH` statement is ever produced or
  executed; write/DDL keywords, multiple statements and destructive requests are
  refused (defence-in-depth, even without a live connection).
- **No hallucination**: table/column names come only from the schema tools;
  unknown fields yield an `unavailable` outcome instead of a guess.
- **Bounded execution**: results are capped by `limit` (with truncation flagged)
  and by a statement `timeout`; timeouts and errors never masquerade as success.
- **Prompt/SQL-injection resistant**: user text is treated strictly as a data
  request; embedded instructions to modify data are refused.