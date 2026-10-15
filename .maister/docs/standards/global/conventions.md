## Development Conventions

### Predictable Structure
Organize files and directories in a logical, navigable layout (see "Domain package structure" below for this project's layout).

### Up-to-Date Documentation
Keep README files current with setup steps, architecture overview, and contribution guidelines (see "README documents setup and limits").

### Clean Version Control
Write clear commit messages scoped to a single task (see "Scoped Conventional Commits per task").

### Environment Variables
Store configuration in environment variables (`MEDICAL_ARCHIVE_*`, see `backend/configuration.md`); never commit secrets or API keys.

### Minimal Dependencies
Keep dependencies lean; document why major ones are included (see "Minimal pins and optional extras").

### Consistent Reviews
Follow a defined code review process with clear expectations for reviewers and authors.

### Testing Standards
Define required tests (unit, integration, negative security cases) before merging; see `testing/test-writing.md`.

### Feature Flags
Use flags for incomplete features instead of long-lived branches.

### Changelog Updates
Record significant user-facing changes and limits in `README.md`.

### Build What's Needed
Avoid speculative code and "just in case" additions (see minimal-implementation.md).

## Project-specific standards (discovered)

### Python 3.12 on Windows / PowerShell
Target Python 3.12 only (`requires-python = ">=3.12,<3.13"`); do not use 3.13+ features. Developer and plan commands are written for PowerShell on Windows and invoked as `py -3.12 -m ...` / `python -m ...`.

### Quality gates before finishing a task
Before completing any task run all three and require exit code 0:
```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```
Ruff is the linter and formatter; max line length is 100; `docs/` is excluded. Use `# noqa: F401` only for intentional side-effect imports (e.g. model registration in `migrations/env.py`).

### Domain package structure
Application code lives under `src/medical_archive/` (setuptools src layout). Each feature is a domain package `src/medical_archive/<domain>/` with `models.py` (SQLAlchemy), `service.py` (business rules + access checks, `Session` as first argument) and `router.py` (module-level `router = APIRouter(prefix="/<domain>", tags=["<domain>"])`), plus focused modules (`storage.py`, `extraction.py`, `retrieval.py`). Shared identity models live in `medical_archive/models/`. `main.create_app()` is the single place that includes routers; domains never import each other's routers; `web/` holds page routes and template rendering. Tests mirror domains under `tests/<domain>/test_<behaviour>.py`.

### English code, Polish UI
Identifiers, docstrings, log messages, API error `detail` and validation messages are in English. Everything a user sees — template text, `title=` passed to `render_template`, form errors, assistant messages (`public_message`, `NO_DATA_ANSWER`) — is in Polish; pages use `<html lang="pl">`.

### Fix the environment, never drop required tests
When native SQLite/OCR library installation fails, fix the environment. Do not delete required tests, skip them silently, or quietly replace the component.

### Never commit secrets, real data or backups
Never commit real medical or user data, `.env`, keys/certificates, SQLite files (incl. `-wal`/`-shm`), uploads or backups; they belong in ignored locations (`data/`, `uploads/`, `user_files/`, `backups/`, `~/.medical-archive`). Tests and acceptance checks use fictitious, non-identifying data only; test databases go in pytest `tmp_path`.

### Scoped Conventional Commits per task
Each task ends with one commit using English Conventional Commit prefixes (`feat:`, `fix:`, `test:`, `build:`, `docs:`) containing only that task's files. If unexpected concurrent commits appear in the shared repo, pause and ask the user before building on them.

### README documents setup and limits
Every feature updates `README.md` (Polish): required versions and configuration, user instructions, and explicit limits (no unit conversion, medical answer limitations, backup/restore procedure, TLS/LAN isolation).

### Minimal pins and optional extras
Runtime dependencies are unpinned except at major-version boundaries that matter (`sqlalchemy>=2,<3`). Dev tools (pytest, httpx, ruff) go in the `dev` extra; heavy local-AI dependencies (pymupdf, sqlite-vec) go in the `local-ai` extra. When an optional dependency is absent, the feature reports an explicit unavailable/limited state (see "No silent fallbacks or fake success" in `error-handling.md`).
