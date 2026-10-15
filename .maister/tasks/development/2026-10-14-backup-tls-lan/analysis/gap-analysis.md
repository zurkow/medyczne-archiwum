# Gap Analysis: Plan 4 — encrypted backup/restore, TLS + LAN-only bind, release checks

## Summary
- **Risk Level**: High
- **Estimated Effort**: High
- **Detected Characteristics**: modifies_existing_code, creates_new_entities, involves_data_operations, ui_heavy (moderate)

Nothing from plan 4 is implemented yet. It needs a new `backup` package, a transactional restore that swaps the live DB under a running app, and a move of `Settings`/`create_app` from fail-open to fail-closed. It also needs three PowerShell scripts, a preflight module and a README runbook. The existing untracked tests in `tests/backup/` pin down the crypto and format contract. Restore, network and release behaviour is defined only in prose.

## Task Characteristics
- Has reproducible defect: no. The fail-open `create_app` and the stale "not ready for real data" README are gaps, not reported defects.
- Modifies existing code: yes (`config.py`, `main.py`, `db.py`, `documents/storage.py` parser, `base.html`, `pyproject.toml`, `README.md`, `.gitignore`, `static/app.css`)
- Creates new entities: yes (`backup/{crypto,format,service,router,restore}.py`, `backup.html`, `ai_preflight`, `scripts/*.ps1`, 3 test modules)
- Involves data operations: yes (create/restore a whole-store backup; restore destructively replaces every entity and wipes sessions)
- UI heavy: moderate (one new admin page with two forms and warnings, plus a nav link)

## Gaps Identified

### Missing Features (verified)
| Gap | Evidence |
|-----|----------|
| No `medical_archive.backup` package | `tests/backup/*` fail at collection on `import medical_archive.backup` |
| No `cryptography` dependency | `pyproject.toml` deps: alembic, argon2-cffi, fastapi, jinja2, pydantic-settings, sqlalchemy, uvicorn |
| No settings for environment, host, port, tls_cert, tls_key, allowed_hosts or backup limits | `config.py` has only secret, data_dir, db url, LM Studio, timeouts and retrieval fields |
| LM Studio loopback check is not in config | Exists only in `rag/embeddings.py:55` and `rag/service.py:77`. The plan's `test_lm_studio_url_must_remain_loopback` expects a `ValidationError` from `Settings(...)` |
| No production validation | No `environment` field and no model validator for TLS/bind |
| No TrustedHost/maintenance middleware, no TLS serving entry point | `main.py` has no middleware. The README runs `python -m uvicorn medical_archive.main:app` (plain http, 127.0.0.1) |
| No `scripts/` directory | `ls scripts` reports "No such file" |
| No importable `ai_preflight` | `tools/check_local_ai.py` is a script that cannot be imported |
| No engine reset hook | `db._session_factory` is `lru_cache(maxsize=1)` and the engine is not exposed |
| No multipart parser for text fields + a large file | `storage.read_multipart_file` reads one `file` field into memory (25 MiB) |
| No backup UI or nav entry | `base.html` admin nav has only `/auth/invite` |
| Test fixtures the plan references do not exist | `backup_client`, `live_database`, `corrupt_backup`, `restore_service`, `traversal_archive`, `settings_factory`, `ai_preflight` |

### Incomplete Features
- **Settings strictness.** `Settings.__init__(testing)` raises a plain `ValueError` when the secret is missing. All other checks are pydantic validators. Production checks must be `model_validator`s so they raise a `ValidationError` that names `tls_cert`. The plan tests call `Settings(...)` without `testing=True`, and that works only because validators run inside `super().__init__` first.
- **`create_app(testing=...)` ignores the flag.** It always uses `Settings(testing=True).data_dir`.
- **Module-level `app = create_app()` runs whenever `medical_archive.main` is imported.** `tests/conftest.py` imports `create_app` from that module. If `create_app()` becomes strict while that line remains, test collection fails whenever `MEDICAL_ARCHIVE_SESSION_SECRET` is not set.
- **Other call sites still fail open:** `documents/service.py:40,51` and `migrations/env.py:27` use `Settings(testing=True)`.

### Behavioral Changes Needed
- Production startup changes from always starting to refusing to start without TLS, a strong secret, a private LAN host and loopback LM Studio.
- Production startup also stops when sqlite-vec or the DB is missing. sqlite-vec is effectively mandatory already, because migration `0004_vector_index` calls `load_sqlite_vec` in upgrade and downgrade. It is still listed only under the `local-ai` optional extra.
- After a restore every session is invalid and users must log in again.
- The README stops saying "not ready for real data" and gains the LAN/TLS/backup/restore runbook.

### Change type
**Additive + modificative.** The backup package and scripts are new. Config, startup and the multipart parser change existing behaviour.

**Compatibility**: moderate.
- Dev and test flows (`Settings(testing=True)`, `create_app(testing=True)`, TestClient over `https://testserver`) must keep working.
- The production start command changes on purpose.

## User Journey Impact Assessment

| Dimension | Current | After | Assessment |
|-----------|---------|-------|------------|
| Reachability | No backup feature exists | Admin nav "Kopia zapasowa" → `/settings/backup`. Offline CLI `scripts/restore-backup.ps1` | OK. Admin only, which is correct |
| Discoverability | 0/10 | 8/10 (top-nav link, next to "Zaproszenia") | +8 |
| Flow integration | n/a | Export is a single POST download. Restore logs everyone out | Warning. The UI must say before submit that a restore logs everyone out, and that the admin's credentials become whatever the backup contains |
| Multi-persona | — | Member: no link, 403 on routes. Admin: full | OK. Needs a test for 403 to member and for redirect when unauthenticated |
| LAN users (phone) | Cannot reach the app (loopback http) | HTTPS on the LAN IP once the mkcert CA is installed on the device | Warning. Depends on manual CA install documented in the README |

## Data Lifecycle Analysis

### Entity: Backup archive (`.marchive`)
| Operation | Backend | UI | Access | Status |
|-----------|---------|-----|--------|--------|
| CREATE (export) | Missing (`create_backup` contract in `test_roundtrip.py`) | Missing (export form) | Missing (nav link) | All in scope |
| READ (validate/inspect) | Missing (`validate_archive`, `validate_payload`) | Restore result message only | — | In scope. No listing is needed because backups live on the client device |
| APPLY (restore) | Missing (RestoreService + offline CLI) | Missing (restore form) | Missing | All in scope |
| DELETE | n/a | n/a | n/a | n/a. Files belong to the admin; server temp/rollback files are deleted in `finally` or after success |

**Completeness after the plan**: 100% for the required operations, provided the HTTP export, HTTP restore and offline CLI restore all land.

**Orphaned operations**: none in the plan.
- One asymmetry: there is an offline restore CLI but no offline export CLI.
- If the web UI breaks before a backup has been taken, nothing can be recovered. This is acceptable, but it is raised as decision I-10.

### Entity: Live store (DB + `documents/`), affected by restore
Restore replaces every table, including `users`, `sessions`, `access_grants`, `documents`, `document_pages`, `rag_chunk_vectors` (vec0), `measurements` and `alembic_version`, as well as every original file.

Touchpoints that need verification after a restore:
- login (accounts come back from the backup)
- grants (isolation between profiles is preserved)
- document download (files match `storage_key`)
- assistant/RAG (vec0 rows present; querying needs sqlite-vec)
- measurements

## Issues Requiring Decisions

Already resolved (not repeated here):
- custom streaming multipart parser
- one-shot v1 envelope with a 1 GiB cap
- sessions wiped after restore
- offline CLI restore

### Critical

**C-1: Live DB swap mechanism on Windows**
`os.replace` over an open SQLite file raises `PermissionError`. The engine is held in an lru_cache, and `documents/` must be swapped together with the DB, which is not atomic as a pair.
- Options:
  - **(A)** Drain, dispose the engine and `cache_clear`, then do a `rename` swap of the DB file and `documents/` directory, with a rollback copy on the same volume and a restore journal checked at startup.
  - **(B)** Copy the validated DB into the live DB page by page with the sqlite online backup API, under a maintenance lock, and swap `documents/` by directory rename. Roll back with a pre-restore snapshot taken the same way.
  - **(C)** Online/HTTP restore only stages the data and the actual swap happens offline on the next start (or via CLI).
- Recommendation: **B**, plus a journal file (`data_dir/.restore-state.json`) checked at startup to finish or roll back an interrupted swap.
- Rationale: B avoids file locks held by stray connections on Windows, keeps the same DB path and inode, and shares code with the offline CLI. The journal covers a crash between the DB step and the files step.

**C-2: Schema revision of the backup differs from the code's alembic head**
The plan recommends taking a backup before a migration, so after an upgrade the only good backup may be at an older revision.
- Options:
  - **(A)** Reject a backup unless its revision equals head.
  - **(B)** Accept an older known revision: run `alembic upgrade head` on the staged copy, then validate.
  - **(C)** Reject a newer or unknown revision; accept an older one only through the offline CLI.
- Recommendation: **B**. Reject unknown or newer revisions; run the upgrade only on the staged copy, before anything touches live data.
- Rationale: A strict equality check makes the backups taken before a migration useless right after the upgrade they protect. Migrating the staged copy keeps the restore non-destructive.

**C-3: Removing module-level `app = create_app()` and the production start path**
Fail-closed `create_app()` cannot run on import, because `tests/conftest.py` imports `medical_archive.main`.
- Options:
  - **(A)** Remove the module-level `app`. Add `python -m medical_archive` (`__main__.serve()`), which builds strict Settings and calls `uvicorn.run(..., ssl_certfile, ssl_keyfile)`. `run-lan.ps1` wraps it.
  - **(B)** Keep `app` but make strict validation lazy (a lifespan/startup hook).
  - **(C)** Use the `uvicorn --factory medical_archive.main:create_app` CLI.
- Recommendation: **A**.
- Rationale: The server cannot start without validated Settings, the TLS paths come from the same validated object, and the README/`run-lan.ps1` need a single entry point. B fails late, after the socket is bound.

### Important

**I-1: Default `environment` value**
- Options:
  - `"development"` default with host restricted to loopback
  - `"production"` default; dev must opt out
- Default: `"development"`, but the validator rejects any non-loopback host unless production. LAN exposure is then impossible without the full production checks.
- Rationale: the fail-closed guarantee then rests on the bind address, and the dev and test flows keep working.

**I-2: Host header restriction (`allowed_hosts`)**
- Options:
  - derive from `host` only
  - `host` plus an explicit `allowed_hosts` list (for a LAN hostname that matches the mkcert cert SANs)
- Default: `host` plus optional `allowed_hosts`. `TrustedHostMiddleware` only in production; no wildcard allowed.
- Rationale: mkcert certificates are often issued for a hostname, and phones may use it.

**I-3: Maintenance mode scope during restore**
- Options:
  - block only mutating requests (the plan text)
  - block every request except `/health` with 503
- Default: block every request except `/health`.
- Rationale: reads during the swap hit a DB that is disposed or half-replaced, and the session wipe logs everyone out anyway.

**I-4: Export memory and size**
One-shot AES-GCM holds the ZIP and the ciphertext in RAM (about 2x the archive). The restore cap is 1 GiB.
- Options:
  - enforce the same configurable cap on export, failing early with a clear message
  - no export cap
- Default: same cap. Compute the estimated size from DB size plus the file sizes before building; skip ZIP compression for already-compressed originals (`ZIP_STORED`) to save CPU.
- Rationale: the app must never create a backup that it would later reject on restore.

**I-5: Backup password policy**
- Options:
  - reuse `validate_password` (12–1024 characters) plus a repeated confirmation field
  - minimum length only
  - none
- Default: reuse `validate_password` and require a confirmation field on export.
- Rationale: a mistyped backup password makes the backup unrecoverable, and the existing policy is already in use.

**I-6: Validating the vector index on restore when sqlite-vec is unavailable**
- Options:
  - fail the restore
  - restore anyway and report degraded RAG
  - require sqlite-vec in production (move it from the `local-ai` extra to main deps)
- Default: treat sqlite-vec as required in production. Migration 0004 already needs it and the plan's preflight stops startup without it. Restore validation runs a count query on `rag_chunk_vectors`.
- Rationale: keeps the dependency story consistent.

**I-7: Account password re-entry and identity after restore**
The admin's password is verified against the current DB. After the restore the admin's account and credentials come from the backup.
- Options:
  - verify against the current DB only, and warn in the UI
  - additionally require that the backup contains at least one admin account (reject otherwise)
- Default: both. Reject a backup with no active admin.
- Rationale: prevents locking everyone out through the web UI. The bootstrap CLI is the fallback.

**I-8: Location of server-side temp, staging and rollback files, and the leftovers policy**
- Options:
  - `data_dir/.backup-tmp/` and `data_dir/.restore-staging/` (same volume, private ACL), cleaned at startup
  - `%TEMP%`
- Default: under `data_dir`, cleaned at startup and in `finally`, with the README noting that Windows has no secure delete.
- Rationale: the same volume is required for the rename, and `%TEMP%` may be shared or synced.

**I-9: Where `ai_preflight` lives and how strict the release gate is**
- Options:
  - `src/medical_archive/preflight.py`, reused by `tools/check_local_ai.py` and `check-release.ps1`, returning ok/degraded/failed
  - a separate tool only
- Default: an importable module with an injectable HTTP probe.
  - degraded (exit 0, warns) for OCR or a missing model
  - failed (non-zero) for sqlite-vec, DB, TLS or data-dir permissions
- Rationale: matches the plan's "stops start" vs "explicit degraded state" split. The test fixture needs an injectable probe.

**I-10: Offline export CLI**
- Options:
  - add `python -m medical_archive.backup.export` alongside the offline restore
  - UI-only export
- Default: UI-only export (the plan scope).
- Rationale: minimal implementation. Raised because restore has an offline path and export does not.

**I-11: Concurrency of export/restore operations**
- Options:
  - a single process-wide lock where a concurrent export or restore gets 409
  - no lock
- Default: lock, with 409 "Operacja kopii zapasowej już trwa."
- Rationale: two restores, or an export during a restore, would corrupt the store.

**I-12: README language and scope**
- Options:
  - Polish (matching the UI and plan)
  - English
- Default: Polish runbook sections covering mkcert, firewall Private-profile rule, LM Studio localhost, backup before update, restore (UI + CLI), lost backup password (unrecoverable), lost admin password (bootstrap CLI), and the isolation check.
- Rationale: the UI and the plan are in Polish.

## Recommendations
1. Do Task 1 first and turn the existing `tests/backup/test_crypto.py` and `test_roundtrip.py` green, keeping their contract exactly:
   - the nonce_len byte
   - `InvalidBackupPassword(InvalidBackup)` for a wrong password and for a tampered tag
   - MAX_* constants read when called
2. Validate the header and size before scrypt, and use the header as AES-GCM AAD. Make the scrypt parameters injectable through the service on `app.state.backup_service` for fast tests.
3. Make `create_backup` take a raw `sqlite3` online backup into `data_dir`-private temp storage, and read storage keys from the snapshot. The manifest carries the sha256 and size of each file; never write absolute paths.
4. Give the restore two phases (stage + validate, then maintenance + swap + health check + rollback) and a journal. Inject the DB path and engine-dispose hook so tests using `tmp_path/auth-test.db` work.
5. Add a `reset_database_engine()` helper in `db.py`.
6. Extend the multipart parser as decided: an allow-list of text fields with per-field caps, exactly one file streamed to disk, and a configurable total cap leading to 413.
7. Put the production model validator and loopback check in Settings. Remove the module-level `app`. Add `python -m medical_archive` serve; `run-lan.ps1` is a thin wrapper.
8. Add tests for:
   - member 403
   - missing CSRF 403
   - wrong account password
   - missing confirmation
   - upload cap 413
   - 422 on wrong password, tamper, traversal or unsupported version, with live bytes compared and unchanged
   - a full roundtrip that brings back accounts, grants, vec rows and originals
   - rollback on an injected swap failure
   - sessions wiped
   - caplog containing no passwords
   - each rejected production host class
9. `check-release.ps1`: ruff check, ruff format --check, pytest, alembic upgrade/downgrade/upgrade on a temp DB (needs sqlite-vec), and preflight. `git status` shows no `.db`, `.marchive`, `.pem` or `.key`. Add `*.marchive` to `.gitignore`.

## Risk Assessment
- **Complexity risk**: High. Crypto, ZIP hardening, a transactional swap with crash recovery, middleware and config hardening all land in one plan.
- **Integration risk**: High.
  - Settings changes touch about 9 call sites.
  - Removing module-level `app` changes how the server is started.
  - Migration 0004 needs sqlite-vec for the release gate.
- **Regression risk**: Medium.
  - Existing tests rely on `create_app(testing=True)` and `Settings(testing=True)`. A production env var set in the developer's shell could leak into them, so the tests must `monkeypatch.delenv`.
  - The multipart parser change touches document upload, so its regression tests must still pass.
- **Data-loss risk**: High if C-1 or C-2 go wrong. The mitigations are the non-destructive staging step, the rollback snapshot, the journal and byte-equality tests.
