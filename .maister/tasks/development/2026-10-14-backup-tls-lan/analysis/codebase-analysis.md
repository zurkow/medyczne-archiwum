# Codebase Analysis Report

**Date**: 2026-10-14
**Task**: Implement plan 4 (`docs/superpowers/plans/2026-10-13-backup-i-lan.md`): encrypted backup/restore, TLS + LAN-only serving, release checks
**Description**: Encrypted backup (AES-256-GCM + scrypt) of the SQLite DB and original files, safe non-destructive restore with an admin UI, TLS + LAN-only bind with fail-closed production config, release checks (`check-release.ps1`) and restore instructions. Existing untracked tests: `tests/backup/test_crypto.py`, `tests/backup/test_roundtrip.py`.
**Analyzer**: codebase-analyzer skill (3 Explore agents: File Discovery, Code Analysis, Context Discovery)

---

## Summary

None of plan 4 is built yet. There is no `medical_archive.backup` package, no `scripts/` directory, no `cryptography` dependency, no host/port/TLS settings, no production validation and no ai_preflight module. The two untracked backup tests fail at collection. The codebase has clear patterns to copy: admin-only CSRF forms in `auth/router.py`, atomic file writes in `documents/storage.py`, injecting services through `app.state`, and the alembic-backed fixtures in `tests/conftest.py`. The hardest parts are a restore that is safe on Windows (the engine is held in an lru_cache, and replacing an open SQLite file fails), a multipart parser that can read text fields as well as a large file, and moving `Settings` from fail-open to fail-closed without breaking the many `Settings(testing=True)` call sites.

The existing untracked tests are more detailed than the code samples in the plan and should be treated as the source of truth (see "Contract from existing tests").

---

## Files Identified

### Primary Files (to create)

**src/medical_archive/backup/crypto.py** (new)
- Envelope encrypt/decrypt, `InvalidBackup`, `InvalidBackupPassword(InvalidBackup)`, `MAX_ENVELOPE_BYTES` (read when called), scrypt constants.

**src/medical_archive/backup/format.py** (new)
- `DATABASE_NAME`, `MANIFEST_NAME`, `MAX_MANIFEST_BYTES` (read when called), ZIP writer, `validate_archive(payload: bytes) -> dict`.

**src/medical_archive/backup/service.py** (new)
- `create_backup(destination, password, *, data_dir, database_url, confirmed_all_profiles=False)`, `BackupConfirmationRequired`, and a RestoreService (`validate_payload`, stage, apply, rollback).

**src/medical_archive/backup/router.py** (new)
- `/settings/backup`: GET page, POST export, POST restore (multipart, 422 on an invalid file).

**src/medical_archive/backup/__init__.py** (new)

**src/medical_archive/templates/backup.html** (new)
- Extends `base.html` like `documents.html` and `measurements.html`. It should not be standalone like `invite.html`.

**tests/backup/test_restore_safety.py**, **tests/test_network_config.py**, **tests/test_release_guards.py** (new)

**scripts/run-lan.ps1**, **scripts/check-release.ps1**, **scripts/restore-backup.ps1** (new)
- `restore-backup.ps1` is in the plan's file map, but no task describes it.

**ai_preflight module in src** (new, location TBD)
- Must be importable and injectable. `.check()` returns `.status` (`"degraded"`) and `.missing_components` (`"embedding"`, `"generation"`, ...). `tools/check_local_ai.py` is a script that cannot be imported, so it can only be used as a reference.

### Primary Files (to modify)

**src/medical_archive/config.py** (77 lines)
- Add `environment`, `host`, `port`, `tls_cert`, `tls_key`, possibly `allowed_hosts` and `backup_max_upload_bytes`. Add an LM Studio loopback validator and a production `model_validator(mode="after")`.
- The custom `__init__(testing)` currently raises a plain `ValueError`.

**src/medical_archive/main.py** (42 lines)
- `create_app(*, testing=False)` ignores `testing` and always uses `Settings(testing=True).data_dir`, so it fails open. Needed changes:
  - wire `backup_router`
  - maintenance-mode and in-flight-request middleware
  - `TrustedHostMiddleware` in production
  - strict Settings validation outside testing
- The module-level `app = create_app()` runs on import.

**src/medical_archive/db.py** (47 lines)
- `_session_factory` uses `lru_cache(maxsize=1)` and builds its own engine, which is not exposed. Add a `reset_database_engine()` / dispose helper.
- No `journal_mode` is set (the default is DELETE).

**src/medical_archive/templates/base.html**
- Line 18 has the admin nav. Add a "Kopia zapasowa" → `/settings/backup` link.

**pyproject.toml**
- Add `cryptography`.

**README.md**
- Replace the "not ready for real data" warning near line 57 with: LAN/TLS (mkcert), firewall, LM Studio on localhost only, backup, restore runbook, update procedure, lost-password procedure, network isolation check.

**static/app.css** (332 lines)
- Styles for the backup page.

### Related Files (reuse / templates)

**src/medical_archive/auth/router.py** (326 lines)
- `invitation_page` L189-205 and `create_invite` L208-231 are the admin GET/POST pattern.
- `_form_fields` L52-70 and `_csrf_or_forbidden` are private and urlencoded-only.

**src/medical_archive/auth/dependencies.py** (101 lines)
- `get_authenticated_session`, `get_current_user`, `require_admin` (403 "Administrator access is required.").

**src/medical_archive/auth/service.py** (259 lines)
- `verify_csrf` L104, `rotate_csrf` L110 (the caller commits), `authenticate_user` L116.

**src/medical_archive/auth/passwords.py**
- `verify_password(password_hash, password)` (argon2id). `validate_password` allows 12–1024 characters.

**src/medical_archive/auth/bootstrap.py** (50 lines)
- argparse + getpass + strict `Settings()`. Template for a restore CLI.

**src/medical_archive/documents/router.py** (481 lines)
- `_verify_owner_action_csrf` L103-115 and `upload_document` L187-221 (X-CSRF-Token header + multipart).
- `download_document` L440-458 (`FileResponse`). `app.state` getattr injection.

**src/medical_archive/documents/storage.py** (218 lines)
- `write_document` writes to a temp file, fsyncs, then `os.replace`s; it has a webroot guard and uses the `.upload-` temp prefix.
- `read_multipart_file` reads a single `file` field into memory with a 25 MiB cap.

**src/medical_archive/web/templates.py**
- `render_template(request, name, *, status_code=200, **context)`.

**src/medical_archive/rag/index.py**
- `load_sqlite_vec`, `rag_chunk_vectors` (vec0), `VectorExtensionUnavailableError`.

**src/medical_archive/rag/embeddings.py:55** and **rag/service.py:77**
- The current LM Studio loopback checks.

**migrations/env.py**
- Reads `MEDICAL_ARCHIVE_DATABASE_URL`. The head is `0005_measurements` (there are two `0002_` revisions).

**tools/check_local_ai.py** (214 lines)
- Preflight reference: sqlite-vec, PyMuPDF, Tesseract+pol, LM Studio `/v1/models`.

**tests/conftest.py** (108 lines)
- Fixtures: `auth_engine` (`tmp_path/auth-test.db` at alembic head), `auth_session_factory`, `auth_db`, `app`, `client` (`https://testserver`), `admin_user` / `member_user` (password "correct horse battery"), `extract_csrf_token`.

**tests/test_database.py**
- Existing Settings validation tests, including `test_settings_rejects_empty_session_secret`.

**tests/auth/test_authentication.py**
- `_login` helper, duplicated in other test packages.

**.gitignore**
- Already ignores `*.key *.pem *.crt certs/ backups/`. Consider adding `*.marchive`.

**work-log.md** (219 lines)
- Groups 1–11 are done (last commit cd9461b); there are no plan-4 entries yet.

---

## Current Functionality

### Key Components/Functions

- **Settings**: has `session_secret` (≥32 bytes), `data_dir` (resolved and created as a side effect), `database_url` (SQLite only), LM Studio URLs/models, timeouts and retrieval parameters.
  - There is no environment, host or TLS setting, and no production validation.
  - Strict `Settings()` is only built in `db._session_factory` and `auth/bootstrap.py`.
  - Everything else uses `Settings(testing=True)`: `main.py:25`, `documents/service.py:40,51` and `migrations/env.py:27`. These read the environment on every call.
- **create_app**: the only place routers are wired. It turns docs off and leaves `/health` unauthenticated. There is no middleware and no host/TLS handling.
  - Startup is `python -m uvicorn medical_archive.main:app`, which binds 127.0.0.1:8000 over plain http.
- **Storage**: originals live at `data_dir/documents/<uuid4>` (36 characters, no extension; `Document.storage_key` is `String(36)` and unique). The file is written before the DB commit, so orphaned files and leftover `.upload-*` temp files are possible.
- **Auth**: secure, httponly, samesite=strict cookies, so TLS is mandatory for LAN use. No rate limiting.
- **Vector index**: vec0 tables live in the same DB, and sqlite-vec is loaded lazily on each connection. The online backup API copies vec0 shadow tables as pages, so it does not need the extension. Queries against `rag_chunk_vectors` do.

### Data Flow (target)

**Export:**
1. Admin POST with CSRF, the account password re-entered, all-profiles confirmation and the backup password.
2. `sqlite3.backup()` snapshots the DB into a private temp directory under `data_dir`.
3. Storage keys are enumerated from the snapshot.
4. ZIP the DB, `files/<key>` and `manifest.json`.
5. Encrypt the ZIP with scrypt → AES-GCM.
6. Return a `FileResponse` attachment with `no-store`; delete the temp files in a BackgroundTask or `finally`.

**Restore:**
1. Multipart upload streamed to disk under a size cap.
2. Decrypt and validate in full (envelope → ZIP safety → manifest → schema revision → `integrity_check` → every key has a file). The live DB is not touched in this phase.
3. Enter maintenance mode, drain in-flight requests, dispose the engine and `cache_clear` it, then run a WAL checkpoint.
4. Take a rollback snapshot on the same volume.
5. Swap the DB and `documents/`, run a health check, and roll back if anything fails.
6. Clear sessions or force re-login, then leave maintenance mode.

---

## Contract from existing tests (source of truth)

### tests/backup/test_crypto.py (53 lines)

- `crypto.encrypt(payload: bytes, password: str) -> bytes` uses a fresh salt and nonce on every call. `crypto.decrypt(envelope, password) -> bytes`.
- Envelope header: `MARCHIVE` (bytes 0-7) | version (byte 8, `\x01`) | salt_len (byte 9, 16) | nonce_len (byte 10, 12) | salt(16) | nonce(12) | ciphertext+tag.
  - The plan text does not include the nonce-length byte; the test requires it.
- A wrong password and a flipped tag byte must both raise `InvalidBackupPassword`. Making `InvalidBackupPassword` a subclass of `InvalidBackup` also satisfies the plan, which expects `InvalidBackup`.
- `InvalidBackup` is raised for:
  - an envelope truncated to 12 bytes
  - a bad magic
  - version `\x02`
  - salt_len `\x0f`
  - nonce_len `\x0b`
- `MAX_ENVELOPE_BYTES` is monkeypatched, so it must be read when the function is called.

### tests/backup/test_roundtrip.py (128 lines)

- `create_backup(destination: Path, password: str, *, data_dir: Path, database_url: str, confirmed_all_profiles: bool=False)`. Without confirmation it raises `BackupConfirmationRequired`.
- **Online backup is required.** The test DB is a raw sqlite3 database in WAL mode with `wal_autocheckpoint=0`, a connection held open during the backup, and the last row present only in `-wal`.
- **Manifest:** `validate_archive(payload) -> dict` must return:
  - `storage_keys == [key]`, taken from the `documents` table
  - `schema_revision == "test_schema_1"`, taken from `alembic_version`
  - `format_version == 1` and `archive_id`
  - no `data_dir` path anywhere in the manifest
- **ZIP entries:** `DATABASE_NAME` (raw sqlite bytes), `files/<storage_key>`, `MANIFEST_NAME`.
- **Rejection:** `../outside.txt` and duplicate entries must be rejected. `MAX_MANIFEST_BYTES` is patched by string path (to 32), so it must be read when called.
- Minor: a test helper is annotated `tuple[Path,str,str]` but returns a connection.

### Plan fixtures (Task 2 and Task 4)

- Task 2 restore tests: `backup_client`, `live_database`, `corrupt_backup`, `restore_service`, `traversal_archive`.
- Task 4 release-guard tests:
  - `settings_factory`: missing TLS must raise a `ValidationError` that matches "tls_cert".
  - `ai_preflight`: reports a degraded status.

The plan's sample tests call fixture-style APIs (`backup_crypto.encrypt(..., password=)`). Follow the existing module-level functions instead.

---

## Dependencies

### Imports (What This Depends On)

- `cryptography` (new): `AESGCM`, and `Scrypt` (or `hashlib.scrypt` with `maxmem=64 MiB`). Parameters: N=2^15, r=8, p=1, 32-byte key.
- stdlib `sqlite3` (online backup API), `zipfile`, `json`, `uuid`, `hashlib`, `tempfile`, `ipaddress`, `os.replace`.
- SQLAlchemy engine and `make_url` (DB path), alembic (schema revision), and `rag.index.load_sqlite_vec` for optional vector validation.
- Internal auth dependencies, CSRF helpers, `verify_password` and `render_template`.
- uvicorn (`ssl_certfile`/`ssl_keyfile`) and Starlette `TrustedHostMiddleware`.

### Consumers (What Depends On This)

- **main.py**: wires the router and middleware.
- **base.html**: nav link.
- **db.py consumers**: every request, because the engine is reset after a restore.
- **Settings call sites**: `main.py`, `documents/service.py`, `migrations/env.py`, `db.py`, `auth/bootstrap.py`, `rag/embeddings.py`, `rag/service.py`.
- **tests/test_database.py**: Settings validation tests.

**Consumer Count**: about 9 files
**Impact Scope**: High. The config changes touch every Settings call site, and the restore swaps the live DB under a running app.

---

## Test Coverage

### Test Files

- **tests/backup/test_crypto.py**: envelope format, wrong password, tampering, header validation, size cap. Currently fails at collection.
- **tests/backup/test_roundtrip.py**: WAL-safe snapshot, manifest, ZIP safety, confirmation. Currently fails at collection.
- **tests/test_database.py**: existing Settings validation.
- **tests/conftest.py**: auth and DB fixtures. Note that `auth_engine` uses `tmp_path/auth-test.db`, not `data_dir/medical_archive.db`, so restore must take its DB path and engine by injection through `app.state`.

### Coverage Assessment

- **Test count**: 2 existing backup test modules. Three planned modules are not written yet.
- **Gaps** (none of the following is tested yet):
  - HTTP restore: admin-only (403 for a member), missing CSRF (403), wrong account password, missing replace confirmation, upload size cap, 422 on corrupt/wrong password with live bytes unchanged.
  - A full restore roundtrip that brings back accounts, metadata, the vector index and originals.
  - Unsupported manifest version, and missing or unexpected ZIP entries.
  - Rollback when the swap fails.
  - No passwords in logs (caplog).
  - Production Settings rejections: 0.0.0.0, loopback, public, multicast and link-local hosts; missing cert or key; weak secret; non-loopback LM Studio URL.
  - `create_app` must not skip validation outside testing mode.
  - Preflight reports degraded status.
  - The PowerShell scripts.

---

## Coding Patterns

### Naming Conventions

- **Modules**: domain packages (`auth`, `documents`, `measurements`, `rag`, now `backup`), each with `router.py`, `service.py` and domain helpers.
- **Functions**: snake_case. Private helpers use a `_` prefix (`_form_fields`, `_csrf_or_forbidden`).
- **Files**: snake_case modules. Templates are lowercase `.html`. UI text is Polish.

### Architecture Patterns

- **Style**: functional FastAPI with `Annotated[..., Depends]` DI. Services are injected through `app.state` with a getattr fallback (`document_indexer`, `document_extractor`, `answer_service`), so tests can swap in fakes. The backup service should follow this (`app.state.backup_service`, with cheap scrypt parameters in tests).
- **State management**: server-rendered Jinja with SQLAlchemy sessions. The CSRF token is rotated on GET and the caller commits.
- **Constraint**: `from __future__ import annotations` is not allowed in `src`.

---

## Complexity Assessment

| Factor | Value | Level |
|--------|-------|-------|
| File Count | about 12 new + about 8 modified | High |
| Dependencies | 9+ (crypto, sqlite3, zipfile, SQLAlchemy, alembic, auth, uvicorn, Starlette middleware, sqlite-vec) | High |
| Consumers | about 9 Settings/db call sites | High |
| Test Coverage | 2 detailed contract tests; HTTP/restore/network/release tests missing | Medium |

### Overall: Complex

The work has four parts: cryptography, a transactional restore on Windows, hardening the network config, and release tooling. They touch cross-cutting concerns (config, DB engine lifecycle, auth, middleware) and the deployment surface.

---

## Key Findings

### Strengths

- Admin + CSRF form patterns already exist, so they can be copied.
- Storage already writes files atomically.
- `app.state` injection makes services testable.
- Alembic-backed test fixtures are in place.
- The existing tests pin down the crypto and format contracts precisely.
- `.gitignore` already excludes certs and backups.
- Docs and OpenAPI are already disabled.

### Concerns

1. **Settings fail open.** `create_app` ignores `testing`, and the plain `ValueError` in `__init__` means the production checks have to be model validators to surface a `ValidationError`. Module-level `app = create_app()` runs on import.
2. **Engine lifecycle.** The `lru_cache` engine is not exposed, so a restore needs dispose + `cache_clear`. On Windows, `os.replace` over an open SQLite file raises `PermissionError`. Consider a page-by-page `validated_conn.backup(live_conn)` under a lock instead.
3. **No multipart parser for text fields.** `read_multipart_file` takes a single field and buffers it in memory (25 MiB). Adding `python-multipart` is a new dependency that work-log marks out of scope. Decide this before Task 2.
4. **The DB and `documents/` swap is not atomic as a pair.** A restore journal (`.restore-state.json`) needs to be checked at startup.
5. **Wrong password and corruption look the same under GCM.** The test requires both to raise `InvalidBackupPassword`. An optional key-check value could tell them apart, but the header layout is fixed by the tests, so do not add one.
6. **One-shot AES-GCM keeps the whole payload in RAM.** Cap the size (`MAX_ENVELOPE_BYTES`). Validate the header and size before running scrypt (DoS).
7. **ZIP safety.** Reject absolute paths, drive letters, backslashes, `..`, duplicates, symlinks, Windows reserved names and `:` (alternate data streams). Enforce per-entry and total uncompressed limits, and check sha256 against the manifest.
8. **Plaintext temp files.** These must stay in a private directory under `data_dir` (not `%TEMP%`) and be deleted in `finally`. Windows has no secure delete; document this in the README.
9. **The restored DB brings back the backup's `sessions` table.** Wipe sessions after a restore.
10. **The plan text and the tests differ** on the nonce-length byte and on which exception a tampered tag raises. `restore-backup.ps1` has no task.

### Opportunities

- Move the LM Studio loopback check into config. There are no remote callers today.
- Add a `__main__.py` `serve()` that builds strict Settings and calls `uvicorn.run(host, port, ssl_certfile, ssl_keyfile, proxy_headers=False, server_header=False)`, so `run-lan.ps1` stays thin.
- Promote `_form_fields` / `_csrf_or_forbidden` to a shared web helper.

---

## Impact Assessment

- **Primary changes**: the new `backup/` package, `config.py`, `main.py`, `db.py`, `backup.html`, `base.html`, `pyproject.toml`, the scripts and the README.
- **Related changes**: `rag/embeddings.py` and `rag/service.py` (loopback check moves to config), `storage.py` (multipart variant), the auth router helpers, and possibly `.gitignore` (`*.marchive`).
- **Test updates**: new `test_restore_safety.py`, `test_network_config.py` and `test_release_guards.py`. Check that `test_database.py` still passes. Tests must not let a production env var leak into the `Settings(testing=True)` call sites.

### Risk Level: High

The restore replaces live medical data and its Windows file-locking behaviour is a known hazard. The config changes affect every Settings caller and app startup. Security-critical paths (crypto, ZIP extraction, auth for destructive operations, TLS/LAN binding) are only partly covered by tests today.

---

## Recommendations

This modifies existing code and adds a new capability. In implementation order:

1. **Task 1: format and crypto.**
   - Add `cryptography`.
   - `crypto.py`:
     - Header layout exactly as the tests require, with the full header passed as AAD.
     - scrypt constants, with injectable parameters for tests.
     - `InvalidBackupPassword(InvalidBackup)`.
     - Validate the header and size before running the KDF.
     - Read `MAX_ENVELOPE_BYTES` when called.
   - `format.py`:
     - A strict validator, with `MAX_MANIFEST_BYTES` read when called.
     - A manifest that also stores sha256 and size per file.
   - `service.create_backup`:
     - Raw `sqlite3.connect(...).backup()` into `data_dir/.tmp-backup-*`.
     - Take storage keys from the snapshot, skip `.upload-*` files and validate the UUID format.
     - Fail if a referenced file is missing.
     - Use the `finally` cleanup.
   - Get both existing tests green first.
2. **Task 2: restore.**
   - Decide the multipart approach first. Recommended: extend the custom parser to stream to disk, with bounded named text fields and a configurable cap. This avoids a new dependency.
   - Build a RestoreService that gets its DB path, engine-dispose hook and data_dir by injection (`app.state`).
   - Phase 1, on a staged copy: validate fully, including the schema revision equal to head, `PRAGMA integrity_check` / `foreign_key_check`, and files present.
   - Phase 2: a maintenance flag in middleware (503 except `/health` and the restore flow), a `threading.Lock`, drain, dispose + `cache_clear`, a rollback copy on the same volume, the swap (prefer the sqlite backup API into the live DB), a health check, rollback on any error, and session wipe.
   - Router: admin-only, CSRF, account password re-entry, explicit replace confirmation, 422 on invalid input. Never log passwords.
   - Export must be a POST, never a GET.
3. **Task 3: network.**
   - Add `environment` (Literal) / `host` / `port` / `tls_cert` / `tls_key`.
   - The production `model_validator(mode="after")` checks that the cert and key exist, and that the host is a private address that is not loopback, unspecified, multicast, link-local or reserved. Error messages must name `tls_cert`.
   - Add the LM Studio loopback validator.
   - `create_app`: use real Settings when not testing, enable `TrustedHostMiddleware` in production, and keep CORS off.
   - Add `__main__.py` serve() and `run-lan.ps1`.
   - README: mkcert, firewall restricted to the private subnet, LM Studio on localhost only.
4. **Task 4: release.**
   - An importable, injectable `ai_preflight` that compares the configured model IDs against `/v1/models` and reports degraded (not failed) when OCR or models are missing.
   - `check-release.ps1`: ruff check, ruff format --check, pytest, alembic up/down/up on a temp DB, and the preflight.
   - README restore runbook.
   - Also write `restore-backup.ps1` (a CLI wrapper modelled on `auth/bootstrap.py`), or drop it from the file map after confirming with the user.

Testing strategy: use the existing contracts as gates. In HTTP tests, put the cheap-KDF service on `app.state`, monkeypatch `MEDICAL_ARCHIVE_DATA_DIR` to `tmp_path` in Settings tests, and use a byte-for-byte comparison of the live DB as the "unchanged" assertion.

---

## Next Steps

Invoke the gap-analyzer with this report. Decisions the user needs to make:

- **Multipart approach:** custom streaming parser or `python-multipart`.
- **Plan/test divergences:** follow the tests on the nonce-length byte and on the exception for a tampered tag.
- **Scope of `restore-backup.ps1`.**
- **Restore swap strategy:** sqlite backup API vs. file rename.
- **Sessions after restore:** wipe them or not.
- **Maximum backup size.**
