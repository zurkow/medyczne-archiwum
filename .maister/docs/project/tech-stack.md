# Technology Stack

## Overview
This document describes the technology choices and rationale for **Medyczne Archiwum** — a local, LAN-only, server-rendered Python web application with an embedded vector store and a local LLM.

## Languages

### Python (3.12, `>=3.12,<3.13`)
- **Usage**: essentially all application, migration, test and tooling code (`src/`, `migrations/`, `tests/`, `tools/`)
- **Rationale**: mature ecosystem for web (FastAPI), ORM (SQLAlchemy), OCR/PDF tooling and local AI clients; pinned to 3.12 for native-extension compatibility (`sqlite-vec`, PyMuPDF) on Windows
- **Key Features Used**: type hints with SQLAlchemy 2 `Mapped[]`, dataclasses/pydantic models, dependency injection via FastAPI `Depends`

### Secondary
- **Jinja2 / HTML** — server-rendered pages, UI in Polish (`lang="pl"`)
- **CSS** — single `static/app.css`, no external CDN assets
- **Vanilla JavaScript / SVG** — measurement trend chart
- **PowerShell** — documented developer commands (Windows host)

## Frameworks

### Frontend
- **Jinja2 templates** (`src/medical_archive/templates/`) rendered by FastAPI; no frontend build tooling or SPA framework.

### Backend
- **FastAPI** (unpinned) — routers per domain, app factory `create_app(testing)` in `main.py`; OpenAPI/docs endpoints disabled.
- **Uvicorn[standard]** (unpinned) — ASGI server, started via `python -m medical_archive` (`serve()`; TLS in production).
- **pydantic-settings** (unpinned) — validated configuration with env prefix `MEDICAL_ARCHIVE_`.
- **argon2-cffi** (unpinned) — Argon2id password hashing.

### Testing
- **pytest** — `pythonpath = ["src"]`; per-test SQLite database migrated via Alembic; FastAPI `TestClient` with an `https` base URL.
- **httpx** — HTTP client used by the test client.
- External services (OCR, LM Studio) are replaced by injected fakes; real-service checks live in `tools/check_local_ai.py` and the sqlite-vec compatibility test.

## Database

### SQLite
- **Type**: relational, single file (default `~/.medical-archive/medical_archive.db`, override with `MEDICAL_ARCHIVE_DATABASE_URL`)
- **ORM/Client**: SQLAlchemy 2 (`>=2,<3`) declarative models; `PRAGMA foreign_keys=ON` on connect
- **Migrations**: Alembic, linear chain `migrations/versions/0001`–`0005`
- **Rationale**: zero-administration local storage for a single home computer; one file simplifies encrypted backup

### sqlite-vec (0.1.9, `local-ai` extra)
- **Type**: vector search extension in the same SQLite database (`vec0` virtual table)
- **Configuration**: 768 dimensions, cosine distance, owner/user partition key
- **Rationale**: avoids a separate search server; partition key lets KNN queries start inside the access boundary

## Local AI & Document Processing
- **LM Studio** — local OpenAI-compatible API on `127.0.0.1:1234` (loopback only, enforced by config)
  - Chat: **Bielik 1.5B v3.0 Instruct** (Polish-language model)
  - Embeddings: **text-embedding-nomic-embed-text-v1.5** (768 dims)
- **PyMuPDF** (`local-ai` extra) — PDF text-layer extraction
- **Tesseract OCR** with `pol.traineddata` — local OCR for images and scanned PDFs (external binary)
- **Rationale**: all processing on the host; no document content, embeddings or queries leave the machine

## Build Tools & Package Management
- **setuptools** (`>=68`) build backend, `src/` layout
- **pip** with editable install: `python -m pip install -e ".[dev]"` (add `local-ai` for OCR/RAG)
- Extras: `dev` (httpx, pytest, ruff), `local-ai` (pymupdf, sqlite-vec)
- **No lock file** currently (reproducibility gap)

## Infrastructure

### Containerization
None — runs directly on the Windows host.

### CI/CD
None configured.

### Hosting
Single home computer, served on the local network only. HTTPS/TLS (mkcert certificates) and LAN-only binding are enforced by the production profile (plan 4). Host disk encryption (e.g. BitLocker) is recommended by the spec.

## Development Tools

### Linting & Formatting
- **Ruff** (0.17.x observed locally) — `line-length = 100`, `exclude = ["docs"]`, default rule set (no explicit `select`)
- Quality gate: `python -m ruff check .` and `python -m ruff format --check .`

### Type Checking
None configured.

### IDE
JetBrains (`.idea/`, currently untracked and not ignored).

## Key Dependencies
| Package | Constraint | Purpose |
|---|---|---|
| fastapi | unpinned | web framework |
| sqlalchemy | `>=2,<3` | ORM |
| alembic | unpinned | schema migrations |
| pydantic-settings | unpinned | config validation |
| argon2-cffi | unpinned | password hashing |
| jinja2 | unpinned | templates |
| uvicorn[standard] | unpinned | ASGI server |
| pymupdf | unpinned (extra) | PDF text |
| sqlite-vec | unpinned (extra, 0.1.9 verified) | vector search |

## Version Management
Versions are declared in `pyproject.toml` only; most are unpinned. Project version `0.1.0`. Commits follow English Conventional Commits (`feat:`, `fix:`, `test:`, `build:`, `docs:`).

---
*Last Updated*: 2026-10-14
*Auto-detected*: languages, dependencies, database, tooling, test setup (from `pyproject.toml`, source tree, README and work-log). *User-provided*: current focus on plan 4.
