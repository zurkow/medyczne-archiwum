# Implementation Plan: Plan 4 — encrypted backup/restore, TLS and LAN-only serving, release checks

## Overview
Total Steps: 65 (sub-steps N.1–N.n across 10 groups)
Task Groups: 10 (9 implementation/verification groups + Test Review & Gap Analysis)
Expected Tests: 45 new test functions in groups 1–8 (several parametrized), plus the 8 existing
test functions in `tests/backup/test_crypto.py` and `tests/backup/test_roundtrip.py` (13 cases),
plus up to 10 gap tests in Group 10, so about 53–63 feature test functions in total.
The spec plans about 40 new tests. The extra ones come from splitting the network and UI work
so that no group has more than 8 tests. This is above the generic 16–34 guideline because of the
scope (17 requirements, high risk, 6 spec test groups).

Source of truth: `implementation/spec.md`. Plan file reference: `docs/superpowers/plans/2026-10-13-backup-i-lan.md`.

### Conventions for every group
- **Test-first.** Write the group's tests, run them and **confirm RED** (record the failure reason:
  ImportError, AttributeError, assertion) before writing production code.
- **Incremental runs.** In step N.n, run only the group's new tests plus the listed regression gates.
- **Quality gates (PowerShell, after every group, before committing):**
  ```powershell
  py -3.12 -m pytest -q
  py -3.12 -m ruff check .
  py -3.12 -m ruff format --check .
  ```
  The full suite must be green between groups.
- **One scoped Conventional Commit per group.** The message is given in the group. Never commit DB,
  `.marchive`, certificate, key or `.env` files.
- **Protected files.** `tests/backup/test_crypto.py` and `tests/backup/test_roundtrip.py` must stay
  byte-identical. Record `Get-FileHash` for both in step 1.1 and re-check it in every group's last step.
- **Coding style.** Absolute `medical_archive` imports, no `from __future__ import annotations`,
  keyword-only options, frozen dataclasses, English code and log text, Polish UI strings copied
  exactly from the spec.

---

## Implementation Steps

### Task Group 1: Encrypted Backup Format (crypto, archive, export)
**Dependencies:** None
**Estimated Steps:** 6
**Commit:** `feat: add encrypted backup format`

- [x] 1.0 Complete the encrypted envelope, the strict archive format and the WAL-safe export
  - [x] 1.1 Write 4 focused tests in the new `tests/backup/test_archive_format.py`. The existing tests are already the RED tests for crypto and roundtrip.
    - `test_archive_validation_rejects_unlisted_missing_and_symlink_entries`, parametrized over: absolute path (`/etc/x`), backslash name (`files\\<uuid>`), drive letter (`C:/x`), `files/not-a-uuid`, an unlisted extra `files/<uuid>`, a missing listed file, and symlink mode bits (`external_attr = 0o120777 << 16`). Use a private helper that builds a valid in-memory ZIP plus manifest and then applies one mutation.
    - `test_archive_validation_rejects_entry_whose_hash_or_size_differs_from_manifest`, covering both a wrong sha256 and a wrong declared size.
    - `test_backup_refuses_export_when_a_referenced_original_is_missing` uses a synthetic store (an `alembic_version` + `documents` table, as in `test_roundtrip.py`) where one row has no file. It expects `BackupSourceError`, no destination file, and an empty `.backup-tmp`.
    - `test_backup_refuses_export_over_size_limit_before_building_archive` uses `max_bytes` smaller than the estimate. It expects `BackupTooLargeError`. No `archive.zip` is ever created (monkeypatch `format.write_archive` to fail the test if called), and `.backup-tmp` is empty.
    - Record `Get-FileHash tests/backup/test_crypto.py, tests/backup/test_roundtrip.py`.
    - Run `py -3.12 -m pytest tests/backup -q`. **Confirm RED** with a collection `ModuleNotFoundError: medical_archive.backup`.
  - [x] 1.2 Dependencies and repo hygiene
    - `pyproject.toml`: add `cryptography` to `[project] dependencies`. Move `sqlite-vec` from the `local-ai` extra to runtime dependencies (`local-ai` keeps `pymupdf`). Keep pins minimal, per conventions.
    - Reinstall: `py -3.12 -m pip install -e ".[dev,local-ai]"`.
    - `.gitignore`: add `*.marchive`.
    - README install section: say that `sqlite-vec` is now part of the base install (one-line edit; the full runbook comes in Group 9).
  - [x] 1.3 `src/medical_archive/backup/__init__.py` (empty) and `backup/crypto.py`
    - Constants: `MAGIC=b"MARCHIVE"`, `FORMAT_VERSION=1`, `SALT_BYTES=16`, `NONCE_BYTES=12`, `HEADER_BYTES=39`, `SCRYPT_N=2**15`, `SCRYPT_R=8`, `SCRYPT_P=1`, `KEY_BYTES=32`, `SCRYPT_MAXMEM=64 MiB`, `MAX_ENVELOPE_BYTES=1 GiB`.
    - `InvalidBackup(Exception)` with the spec docstring, and `InvalidBackupPassword(InvalidBackup)`.
    - `encrypt`: fresh `os.urandom` salt and nonce, the header as AAD, `AESGCM`. Raises `ValueError` if the result would exceed `MAX_ENVELOPE_BYTES`.
    - `decrypt` checks in the exact spec order (size > global looked up at call time, then length < header + 16, then magic/version/salt_len/nonce_len, then the KDF, then `AESGCM.decrypt`). `InvalidTag` raises `InvalidBackupPassword` `from None`.
  - [x] 1.4 `backup/format.py`
    - Constants `MANIFEST_NAME`, `DATABASE_NAME`, `FILES_PREFIX`, `ARCHIVE_FORMAT_VERSION=1`, `MAX_MANIFEST_BYTES=1 MiB`.
    - Private `_parse_manifest` with a strict exact key set:
      - canonical UUID4 check `str(uuid.UUID(v)) == v` and version 4 for `archive_id`;
      - canonical lowercase UUIDs for `storage_keys`, sorted and unique, with `files` keys equal to `storage_keys`;
      - revision regex `^[0-9A-Za-z_]{1,64}$`;
      - sha256 regex `^[0-9a-f]{64}$`;
      - ints ≥ 0.
    - `write_archive`: `manifest.json` and `database.sqlite3` DEFLATED, `files/<key>` STORED, no directory entries. Files are streamed in 64 KiB chunks with sha256 computed on the fly. Returns the manifest.
    - `validate_archive` implements the 6 ordered checks (entry count ≤ 2 + 100 000; name allow-list; duplicates, directory entries, symlink bits, encryption flag, compression type; bounded manifest read with `MAX_MANIFEST_BYTES` looked up at call time; exact name-set equality; declared sizes, total ≤ `crypto.MAX_ENVELOPE_BYTES`, capped streaming reads of size + 1 and sha256 comparison).
    - `BadZipFile`, `json.JSONDecodeError` and `UnicodeDecodeError` are wrapped in `InvalidBackup`.
    - `extract_archive` validates first, then writes with `"xb"` to `destination/database.sqlite3` and `destination/documents/<key>`.
  - [x] 1.5 `backup/service.py`, export part only
    - Errors: `BackupConfirmationRequired`, `BackupTooLargeError`, `BackupSourceError`.
    - Private `_sqlite_path(database_url)`: SQLite only, otherwise `BackupSourceError`.
    - Private `_write_atomic(path, data)`: temp file in the same directory, fsync, `os.replace`. Reuse the pattern of `documents/storage.write_document`.
    - `create_backup(...)` follows the 12 spec steps:
      1. confirmation check before any I/O, then `validate_password`;
      2. work in `data_dir/.backup-tmp/<uuid4>/`;
      3. snapshot with the backup API (both connections closed in `finally`), then `journal_mode=DELETE`;
      4. read the revision (exactly one row) and the storage keys (canonical UUID + regular file) from the **snapshot**;
      5. size estimate (snapshot + files + 4 KiB/entry + 64 KiB) checked against `max_bytes` (default `crypto.MAX_ENVELOPE_BYTES` at call time) **before** `write_archive`;
      6. encrypt, then check the envelope size again;
      7. atomic write of the destination;
      8. log `backup_export_succeeded archive_id=%s duration_ms=%d` / `backup_export_failed code=%s`;
      9. `shutil.rmtree(work)` in `finally`.
    - Never log the password, paths or filenames.
  - [x] 1.6 Ensure the format tests pass
    - Run `py -3.12 -m pytest tests/backup/test_crypto.py tests/backup/test_roundtrip.py tests/backup/test_archive_format.py -q`.
    - Verify that both protected files are hash-identical to step 1.1.
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 4 new tests and the unmodified `test_crypto.py` and `test_roundtrip.py` (8 functions, 13 cases) pass.
- `sqlite-vec` and `cryptography` are runtime dependencies, and `*.marchive` is ignored.
- No plaintext remains under `.backup-tmp` after a successful or failed export.

---

### Task Group 2: Fail-closed Settings (network, TLS, LM Studio, backup cap)
**Dependencies:** None (ordered after 1 to keep a linear history)
**Estimated Steps:** 7
**Commit:** `feat: add fail-closed production settings`

- [x] 2.0 Complete the fail-closed `Settings` profile
  - [x] 2.1 Add the test isolation fixture and write 6 focused tests in the new `tests/test_network_config.py`
    - Autouse fixture in `tests/conftest.py`: `monkeypatch.delenv(..., raising=False)` for `MEDICAL_ARCHIVE_ENVIRONMENT`, `_HOST`, `_PORT`, `_TLS_CERT`, `_TLS_KEY`, `_ALLOWED_HOSTS`, `_BACKUP_MAX_BYTES` and `_DATABASE_URL`. Do not touch other existing test files.
    - Local fixture: `tls_files(tmp_path)` writes dummy cert and key files.
    - `test_production_config_refuses_public_bind_and_missing_tls` (as written in plan 4, Task 3).
    - `test_lm_studio_url_must_remain_loopback` (as in the plan; add cases for a remote host, userinfo, a query and a non-http scheme).
    - `test_production_rejects_every_non_private_lan_host_class`, parametrized over `0.0.0.0`, `::`, `127.0.0.1`, `::1`, `224.0.0.1`, `169.254.1.1`, `fe80::1`, `8.8.8.8`, `100.64.0.1`. Assert that the error mentions `host must be an explicit private LAN address`.
    - `test_production_accepts_private_lan_host_with_existing_tls_files` (`192.168.1.10`, 32-byte secret, existing files).
    - `test_development_rejects_non_loopback_host`.
    - `test_allowed_hosts_rejects_wildcards` (`*`, `*.lan`; an uppercase hostname is lowercased).
    - **Confirm RED**.
  - [x] 2.2 Add the new fields to `src/medical_archive/config.py`
    - `environment: Literal["development","production"]="development"`;
    - `host: str="127.0.0.1"`;
    - `port: int = Field(8000, ge=1, le=65535)`;
    - `tls_cert`/`tls_key: Path | None` (expanded + resolved);
    - `allowed_hosts: list[str] = []` (JSON list from env);
    - `backup_max_bytes: int = Field(1 GiB, ge=1 MiB, le=1 GiB)`.
  - [x] 2.3 `lm_studio_base_url` field validator for every environment: scheme `http`/`https`, host in {127.0.0.1, localhost, ::1}, no username, password, query or fragment. Use the exact spec message. Keep the client-side checks in `rag/embeddings.py` and `rag/service.py` unchanged.
  - [x] 2.4 Validators for `host` (must parse with `ipaddress.ip_address`) and `allowed_hosts` (lowercase; DNS labels `[a-z0-9-]` of 1–63 chars with a total ≤ 253, or an IP literal; any `*` rejected).
  - [x] 2.5 Validators across fields
    - TLS pairing in every environment: both set or both unset, and existing files when set.
    - `model_validator(mode="after")`:
      - development requires a loopback `host`;
      - production collects every problem (secret, cert, key, host class) into one `ValueError("Invalid production configuration: " + "; ".join(problems))`.
    - Keep the existing `__init__(testing)` secret check unchanged.
  - [x] 2.6 Audit the call sites
    - `rg "Settings\(" src tests tools migrations` and `rg "lm_studio_base_url" tests`.
    - Make sure `Settings(testing=True)` defaults stay valid and that no existing test builds Settings with a non-loopback LM Studio URL. If one does, adjust that test's synthetic URL (never the protected backup tests).
  - [x] 2.7 Ensure the settings tests pass
    - Run `py -3.12 -m pytest tests/test_network_config.py tests/test_database.py -q` (regression gate: `tests/test_database.py`).
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 6 tests pass and `tests/test_database.py` stays green.
- A production misconfiguration produces one `ValidationError` that names every offending field.
- The developer shell environment cannot leak into tests (autouse fixture).

---

### Task Group 3: Strict App Startup and TLS Serving
**Dependencies:** 2
**Estimated Steps:** 7
**Commit:** `feat: serve app over trusted lan tls`

- [x] 3.0 Complete strict `create_app`, the engine reset and `python -m medical_archive`
  - [x] 3.1 Write 4 focused tests
    - In `tests/test_network_config.py`:
      - `test_production_app_rejects_foreign_host_header_and_sends_no_cors_headers`: build valid production Settings (`192.168.1.10`, `allowed_hosts=["archiwum.lan"]`, TLS files, secret, `data_dir=tmp_path`) and call `create_app(settings=...)`. `Host: evil.example` returns 400. `Host: archiwum.lan` on `/health` returns 200 with no `access-control-allow-origin`, even when an `Origin` header is sent. `/docs` and `/openapi.json` return 404.
      - `test_create_app_outside_testing_refuses_to_start_without_session_secret`: with no secret in the env, `create_app()` raises.
      - `test_serve_runs_uvicorn_with_tls_and_without_proxy_headers`: set the production env vars via monkeypatch and capture the `uvicorn.run` kwargs. Expect host, port, `ssl_certfile`, `ssl_keyfile`, `proxy_headers=False`, `server_header=False`.
    - In `tests/test_database.py`: `test_reset_database_engine_disposes_cached_engine_and_rebuilds_on_next_use` (monkeypatch `MEDICAL_ARCHIVE_DATABASE_URL` and the secret to a tmp DB).
    - **Confirm RED**.
  - [x] 3.2 `src/medical_archive/db.py`
    - Split into an `lru_cache` `_database_engine()` (strict `Settings()`) and `_session_factory()` bound to it.
    - Add `reset_database_engine() -> None`: dispose the cached engine if one exists, then `cache_clear()` both caches.
    - Keep the public session dependency behaviour unchanged.
  - [x] 3.3 `src/medical_archive/main.py`
    - `create_app(*, testing: bool = False, settings: Settings | None = None)` and `settings = settings or Settings(testing=testing)`.
    - Set `app.state.data_dir`.
    - In production, add `TrustedHostMiddleware(allowed_hosts=[settings.host, *settings.allowed_hosts])` **last**, so it is outermost. No CORS. Keep docs/redoc/openapi as `None`.
    - **Remove** the module-level `app = create_app()`.
    - `rg "main:app|main import app"` must find nothing in `src`, `tests`, `README.md`, `tools` or `scripts`.
  - [x] 3.4 `src/medical_archive/__main__.py`: `serve()` runs strict `Settings()`, then `create_app(settings=settings)`, then `uvicorn.run(app, host=..., port=..., ssl_certfile=..., ssl_keyfile=..., proxy_headers=False, server_header=False)`, with `if __name__ == "__main__": serve()`. The preflight gate is added in Group 8. Development with no TLS runs plain http on loopback.
  - [x] 3.5 `scripts/run-lan.ps1`
    - `param([Parameter(Mandatory)][string]$HostAddress, [int]$Port = 8443, [Parameter(Mandatory)][string]$CertFile, [Parameter(Mandatory)][string]$KeyFile)`, with `$ErrorActionPreference = "Stop"`.
    - Fails with a Polish message if the cert or key is missing or `MEDICAL_ARCHIVE_SESSION_SECRET` is unset. It never generates certificates.
    - Sets `MEDICAL_ARCHIVE_ENVIRONMENT=production`, `_HOST`, `_PORT`, `_TLS_CERT` and `_TLS_KEY` for the process, then runs `python -m medical_archive`.
  - [x] 3.6 README start command: replace `python -m uvicorn medical_archive.main:app` with `python -m medical_archive` (development, loopback http) and `.\scripts\run-lan.ps1 ...` (production). This keeps the README in sync in the same group that removes the module-level app.
  - [x] 3.7 Ensure the startup tests pass
    - Run the 4 new tests plus `tests/test_health.py` and `tests/test_database.py`.
    - Parse-check the script: `[System.Management.Automation.Language.Parser]::ParseFile("scripts/run-lan.ps1",[ref]$null,[ref]$errors)` must report no errors.
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 4 tests pass. `create_app(testing=False)` fails closed. TrustedHost is active only in production, and no CORS headers are sent.
- No reference to the module-level `app` remains, and the README start command works.

---

### Task Group 4: Streaming Multipart Form Parser and Shared Form Helper
**Dependencies:** None (ordered after 3)
**Estimated Steps:** 5
**Commit:** `feat: add streaming multipart form parser`

- [x] 4.0 Complete `read_multipart_form` and the `web/forms.py` move
  - [x] 4.1 Write 5 focused tests in the new `tests/documents/test_multipart_form.py`
    - Drive the parser through a minimal Starlette `Request` built from a scope plus a chunked `receive`, following the approach used by `tests/documents/test_upload.py`.
    - `test_multipart_form_streams_file_to_disk_and_returns_allowed_text_fields`: the file is split across several chunks, and the returned `MultipartForm.file_size` and the bytes on disk match.
    - `test_multipart_form_calls_before_file_before_writing_any_file_bytes`: the callback raises a 403 `UploadError`, and the destination does not exist.
    - `test_multipart_form_rejects_unknown_or_duplicate_text_fields`, parametrized (400).
    - `test_multipart_form_rejects_file_over_cap_and_removes_partial_file` (413; the destination is gone).
    - `test_multipart_form_rejects_oversized_content_length_before_reading_body`: `receive` fails the test if called; expect 413.
    - **Confirm RED**.
  - [x] 4.2 Extract the private part iterator in `documents/storage.py`
    - Factor the boundary and header state machine out of `read_multipart_file` into a private async generator that yields part headers and data chunks.
    - Re-implement `read_multipart_file` on top of it with **identical** messages and limits.
    - Run `py -3.12 -m pytest tests/documents/test_upload.py -q`; it must pass with the file unchanged.
  - [x] 4.3 Add `read_multipart_form` and a frozen dataclass `MultipartForm(fields, file_size)`, following the spec's "Multipart parser contract":
    - 415 for a bad content type or boundary;
    - an early 413 from `Content-Length` over `max_file_bytes + 64 KiB`;
    - text allow-list, UTF-8 and a `max_field_bytes` limit;
    - at most `len(text_fields) + 1` parts, header blocks ≤ 16 KiB, nothing after the closing boundary, client disconnect, and a missing file part, each giving 400;
    - exactly one file part, written to `destination` opened with `"xb"`, with `before_file(fields_so_far)` called before the first byte;
    - the partial file is deleted on any error;
    - filename and MIME type are ignored and never logged.
  - [x] 4.4 Create `src/medical_archive/web/forms.py` with `read_form_fields(request, *, max_bytes=16_384, max_fields=16)`, moved unchanged from `auth/router._form_fields` (415, 413, 400 duplicate). `auth/router.py` imports it, and the local helper is deleted. Leave the parsers in `documents/router.py` and `measurements/router.py` alone (out of scope).
  - [x] 4.5 Ensure the multipart tests pass
    - Run the 5 new tests plus the regression gates `tests/documents/test_upload.py` and `tests/auth`.
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 5 tests pass, and `tests/documents/test_upload.py` passes unchanged.
- The state machine is shared, not duplicated, and auth form handling behaves the same.

---

### Task Group 5: Verified Restore Service (staging, journal, rollback, recovery)
**Dependencies:** 1, 3
**Estimated Steps:** 10
**Commit:** `feat: add verified backup restore`

- [x] 5.0 Complete the non-destructive two-phase restore with journal-based recovery
  - [x] 5.1 Write 8 focused tests in the new `tests/backup/test_restore_safety.py`, plus the new `tests/backup/conftest.py`
    - Write the **rollback and crash-recovery tests first**, as the spec-writer warned.
    - `tests/backup/conftest.py` provides:
      - `backup_data_dir` (`tmp_path/"data"` with `documents/`);
      - `backup_service`, a real `BackupService(data_dir=..., database_url=str(auth_engine.url), max_bytes=<small, configurable>, dispose_engine=auth_engine.dispose)`;
      - a `make_envelope` fixture that wraps a private `_make_envelope` helper (builds a ZIP via `format.write_archive` or raw `zipfile` for malicious cases, then `crypto.encrypt`);
      - a seeding helper for the live DB plus original files, using synthetic data.
    - Tests:
      - `test_failed_swap_rolls_back_database_and_documents`: monkeypatch the private `_swap_documents` step to raise `OSError`. Expect `RestoreFailedError`, live `iterdump()` unchanged, the documents listing unchanged, sessions intact, and no journal or operation dir left.
      - `test_interrupted_restore_is_rolled_back_at_startup`: the private step raises a test-local `BaseException` subclass. Afterwards the journal exists. `recover_backup_state(...)` must then bring back the previous content and leave no `.restore-staging` or `.backup-tmp` leftovers.
      - `test_bad_backup_never_replaces_existing_database`, parametrized over corrupt bytes, wrong password, flipped tag, traversal archive and unsupported envelope version. Expect `InvalidBackup`, live DB **file bytes** identical, and the documents listing identical.
      - `test_restore_rejects_path_traversal_entry_before_database_swap`.
      - `test_restore_rejects_backup_without_admin_account` (`MissingAdministratorError`).
      - `test_restore_rejects_newer_schema_revision` (`UnsupportedBackupRevisionError` for an unknown revision such as `9999_future`).
      - `test_restore_upgrades_older_revision_only_on_staged_copy`: build the backup DB at `0004_vector_index` and `monkeypatch.setenv("MEDICAL_ARCHIVE_DATABASE_URL", <live url>)`. The live DB is byte-identical after `stage_restore`, and the staged DB is at head.
      - `test_restore_and_export_never_log_passwords`: caplog at DEBUG over an export, a failed restore and a successful restore. Neither password appears in any record.
    - **Confirm RED**.
  - [x] 5.2 `migrations/env.py` URL precedence: programmatic `sqlalchemy.url` first, then `MEDICAL_ARCHIVE_DATABASE_URL`, then `Settings(testing=True)`. `alembic.ini` keeps the URL empty, so CLI behaviour is unchanged. Add a short "why" comment (an env var must never redirect a staged upgrade to the live DB).
  - [x] 5.3 Errors and primitives in `backup/service.py`
    - `BackupBusyError`, `UnsupportedBackupRevisionError(InvalidBackup)`, `MissingAdministratorError(InvalidBackup)`, `RestoreFailedError`, `RestoreRollbackFailedError(RestoreFailedError)`.
    - Frozen `StagedRestore(operation_dir, database_path, documents_dir, archive_id, schema_revision)`.
    - `backup_operation_lock()`: a context manager over a module-level `threading.Lock` using `acquire(blocking=False)`. It raises `BackupBusyError` when the lock is held.
  - [x] 5.4 `stage_restore` (phase 1, never touches live data), following the spec's 9 steps:
    1. on-disk size ≤ `max_bytes`;
    2. read the bytes, unlink the upload, `decrypt`;
    3. `extract_archive` to `operation_dir/staged`;
    4. raw `sqlite3` `integrity_check` == `ok` and an empty `foreign_key_check`;
    5. revision classification with alembic `ScriptDirectory`, resolving `alembic.ini` and `migrations` the same way as `tests/conftest.py` (project root): head is OK, a known older revision runs `command.upgrade(cfg, "head")` with `sqlalchemy.url` set to the staged path and then re-checks, anything else raises `UnsupportedBackupRevisionError`;
    6. admin count ≥ 1;
    7. `load_sqlite_vec` + `SELECT COUNT(*) FROM rag_chunk_vectors` (`VectorExtensionUnavailableError` raises `RestoreFailedError`, any other `sqlite3.Error` raises `InvalidBackup`);
    8. DB storage keys == manifest keys;
    9. `OSError` anywhere raises `RestoreFailedError`.
  - [x] 5.5 Journal helpers
    - `_write_journal(state)`: atomic (temp, fsync, `os.replace`) at `data_dir/.restore-journal.json` with keys `journal_version`, `operation_id`, `state` and `live_documents_existed`. No absolute paths.
    - `_read_journal()`: strict validation; an invalid or unreadable journal raises `RestoreFailedError`.
  - [x] 5.6 `apply_restore` (phase 2), with private, monkeypatchable step functions: `_snapshot_live_database`, `_copy_staged_database`, `_swap_documents`, `_wipe_sessions`, `_health_check`.
    1. `dispose_engine()`;
    2. snapshot into `rollback/database.sqlite3.partial`, rename it, then write `prepared`;
    3. backup-API copy staged → live, then `database_replaced`;
    4. rename `documents` → `rollback/documents`, then `staged/documents` → `documents`, then `documents_replaced`;
    5. `DELETE FROM sessions`;
    6. health check (quick_check, head revision, admin count ≥ 1, zero sessions, every key has a file), then `committed`;
    7. remove the operation dir, remove the journal, and log `backup_restore_applied archive_id=%s duration_ms=%d`.
    - Close every `sqlite3` connection before renames (Windows handle locks).
  - [x] 5.7 `_rollback(operation_id)`
    - Catch `Exception` (not `BaseException`) in steps 3–6.
    - Restore the documents dir (move the current one aside into the operation dir, rename `rollback/documents` back), then copy `rollback/database.sqlite3` → live via the backup API.
    - Delete the journal and the operation dir, log `backup_restore_rolled_back code=%s`, and raise `RestoreFailedError`.
    - If the rollback itself fails: keep the journal and the rollback data, log `backup_restore_rollback_failed`, and raise `RestoreRollbackFailedError`.
    - The rollback snapshot is never deleted in a generic `finally`.
  - [x] 5.8 `recover_backup_state(*, data_dir, database_url)`
    - Journal states `prepared`, `database_replaced` and `documents_replaced` are always rolled back. `committed` only gets its cleanup.
    - Then remove every entry under `.backup-tmp/` and `.restore-staging/` that no unresolved journal references.
    - Add `new_operation_dir()` and `discard(operation_dir)` (no-op while the journal references it).
    - Add the `BackupService` class (keyword-only `__init__`, a `max_bytes` property, methods that delegate to the module functions).
  - [x] 5.9 Wire it into `create_app(testing=False)`: call `recover_backup_state(data_dir=settings.data_dir, database_url=settings.database_url)`, then set `app.state.backup_service = BackupService(..., max_bytes=settings.backup_max_bytes, dispose_engine=reset_database_engine)`. The testing app gets no service (the router raises `RuntimeError` later). Check that the Group 3 production-app test still passes, since recovery runs harmlessly on its tmp `data_dir`.
  - [x] 5.10 Ensure the restore tests pass
    - Run `py -3.12 -m pytest tests/backup -q`.
    - Verify the protected file hashes.
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 8 tests pass. Every rejected restore leaves the live DB byte-identical and `documents/` unchanged.
- A failed swap rolls back before returning. An interrupted swap is rolled back at startup and never rolled forward.
- No plaintext remains under `.backup-tmp` or `.restore-staging` after a completed, failed or recovered operation. Logs contain only opaque ids, codes and durations.

---

### Task Group 6: Backup Page, Export Route and Login Notice (UI)
**Dependencies:** 4, 5
**Estimated Steps:** 7
**Commit:** `feat: add admin backup page with encrypted export`

- [x] 6.0 Complete the admin backup page and the export download
  - [x] 6.1 Write 6 focused tests in the new `tests/backup/test_backup_ui.py`, and extend `tests/backup/conftest.py`
    - Conftest additions: `backup_app` (sets `app.state.data_dir` and `app.state.backup_service`), a private `_login` helper copied from the `tests/auth/test_authentication.py:15` pattern, `admin_client`, `member_client`.
    - `test_member_gets_403_and_anonymous_is_redirected_from_backup_routes`, parametrized over `GET /settings/backup` and `POST /settings/backup/export` (the restore route case is added in 7.1).
    - `test_backup_post_without_csrf_is_forbidden_and_leaves_no_temp_files`, with an export case (the restore case is added in 7.1). Expect 403 with the plain `Invalid or missing CSRF token.` and an empty `.backup-tmp`.
    - `test_wrong_admin_password_or_missing_confirmation_is_rejected_without_changes`, with export cases: wrong admin password (403, `Hasło administratora jest nieprawidłowe.`), confirmation missing (400), repeat mismatch (400), and a password shorter than 12 (400). Assert `EXPORT_SUMMARY`, the exact field-error text, `aria-invalid="true"`, exactly one `role="alert"`, and that no password value is echoed (restore cases are added in 7.1).
    - `test_concurrent_backup_operation_returns_409`: hold `backup_operation_lock()` inside the test. Export returns 409 `Operacja kopii zapasowej już trwa.` (the restore case is added in 7.1).
    - `test_export_downloads_no_store_attachment_and_removes_server_copy`: 200 `application/octet-stream`, `Content-Disposition: attachment; filename="medyczne-archiwum-YYYYMMDD-HHMMSS.marchive"`, `Cache-Control: no-store`. The body decrypts with the backup password and validates. After the response, `.backup-tmp` holds no `.marchive`.
    - `test_login_notice_ignores_unexpected_restored_values`: `?restored=1` shows `Kopia została odtworzona. Zaloguj się ponownie.`. `?restored=<script>` shows no notice and nothing is reflected.
    - **Confirm RED**.
  - [x] 6.2 `src/medical_archive/backup/router.py`, part 1
    - `router = APIRouter(prefix="/settings/backup", tags=["backup"], include_in_schema=False)`.
    - `GET ""` with `require_admin`.
    - `POST /export` following "Export orchestration":
      1. `read_form_fields`, then `verify_csrf` (403 plain);
      2. `verify_password` (403 page);
      3. collect field errors in one pass (`confirm`, `hmac.compare_digest` for the repeat, the `validate_password` `ValueError` mapped to the length message), giving 400;
      4. lock (409);
      5. `run_in_threadpool(service.create_backup, data_dir/.backup-tmp/<uuid>.marchive, ...)`, with `BackupTooLargeError` giving 413 and `BackupSourceError` giving 500 `Nie można utworzyć kopii. Szczegóły zapisano w dzienniku aplikacji.`;
      6. `FileResponse` with the UTC filename, `Cache-Control: no-store` and a `BackgroundTask` that unlinks the file.
    - Polish catalogue constants (`EXPORT_SUMMARY`, `RESTORE_SUMMARY` and all spec strings) are kept as module constants.
    - A private `_render_backup_page(request, database, *, status, export_alert, export_errors, restore_alert, restore_errors)` rotates CSRF, commits and renders.
    - A private `_size_label(max_bytes)` gives whole GiB, otherwise whole MiB.
    - A missing `app.state.backup_service` raises `RuntimeError`.
  - [x] 6.3 `templates/backup.html`: extends `base.html`; `section.panel[aria-labelledby=backup-heading]`, intro, `div.info-box`, the export section, and the restore `section.danger-zone`. The restore form markup is part of the binding page structure; its POST handler comes in Group 7. Ids, names, attributes and exact Polish strings come from the spec tables. The csrf hidden input is first in both forms. There is no JS and no meta refresh. Passwords are never pre-filled and checkboxes are never re-checked.
  - [x] 6.4 Navigation and login
    - `templates/base.html`: inside `{% if user.is_admin %}`, after "Zaproszenia", add `<a href="/settings/backup">Kopia zapasowa</a>` with `aria-current="page"` when the path starts with `/settings`.
    - `templates/login.html`: add the `/static/app.css` link and an optional `<p role="status" class="notice-success">` with both notice sentences, shown when `notice == "restored"`.
    - `auth/router.py` `login_page`: add the query parameter `restored: str | None = None` and pass `notice="restored"` only when it equals `"1"`. The value is never reflected.
  - [x] 6.5 Append about 45 lines to `static/app.css`: `.warning-box`, `.warning-box-danger`, `.warning-box ul`, `.info-box`, `.checkbox-field` (+ checkbox override), `input[type="file"]` (`max-width:100%`), `.field-hint`, `.field-error`, `input[aria-invalid="true"]`, `button.button-danger` (+ hover), `.danger-zone`, `[role="status"].notice-success`. Use the mockup colour values, rem units and contrast ≥ 4.5:1.
  - [x] 6.6 `create_app`: `include_router(backup_router)`, wired only there.
  - [x] 6.7 Ensure the UI tests pass
    - Run `tests/backup/test_backup_ui.py` plus `tests/auth` (login page regression).
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 6 tests pass. An admin can download an encrypted backup with no server copy left behind. A member gets 403 and an anonymous user is redirected.
- The exact Polish strings and the accessibility attributes match the spec, and the login notice cannot be injected.

---

### Task Group 7: Web Restore Flow and Maintenance Mode
**Dependencies:** 6
**Estimated Steps:** 6
**Commit:** `feat: restore backups from the admin page under maintenance mode`

- [x] 7.0 Complete the web restore orchestration, the maintenance middleware and the acceptance roundtrip
  - [x] 7.1 Write 6 new focused tests and extend 4 of the Group 6 parametrizations with restore cases
    - Extensions:
      - the member/anonymous test gets `POST /settings/backup/restore`;
      - the CSRF test gets a restore case (multipart **without** a `csrf_token` before the file, which gives 403 with no upload file left in `.restore-staging`);
      - the field-error test gets restore cases: wrong admin password 403, confirmation missing 400, no file or empty file 400 `Wybierz plik kopii (.marchive).`, all with `RESTORE_SUMMARY`;
      - the 409 test gets a restore case.
    - New in `tests/backup/test_backup_ui.py`:
      - `test_restore_with_corrupt_or_wrong_password_backup_returns_identical_422_and_keeps_live_data`: both responses are 422 with the identical alert text, and the live DB bytes and documents are unchanged.
      - `test_restore_over_size_cap_returns_413`: the small `max_bytes` fixture. Expect 413 with the size label and `restore-file` marked invalid.
      - `test_full_backup_restore_roundtrip_returns_accounts_grants_documents_vectors_and_measurements` (acceptance 6):
        - seed an admin, a member, a grant, a document with a page and an original file, a vec0 row and a measurement;
        - export over HTTP, then mutate the live store;
        - restore over HTTP. Expect a 303 to `/auth/login?restored=1` and the cookie deleted. The old cookie now redirects to login;
        - the login page shows the notice;
        - the original state is back, with byte-equal file content.
      - `test_maintenance_mode_returns_503_for_everything_except_health`: with the state active, `/`, `/static/app.css` and `/auth/login` return 503 with `Retry-After: 30`, `Cache-Control: no-store` and the maintenance h1. `/health` returns 200.
    - New in `tests/backup/test_maintenance.py`: `test_maintenance_drain_waits_for_other_in_flight_requests` (async; `wait_until_drained` returns True only once `in_flight` falls to 1, and False on timeout).
    - **Confirm RED**.
  - [x] 7.2 `backup/maintenance.py`
    - `MaintenanceState` (`active`, `in_flight`, `enter()`, `exit()`, `async wait_until_drained(*, timeout_seconds) -> bool`, polling with `asyncio.sleep`).
    - Pure ASGI `MaintenanceMiddleware(app, *, state)`:
      - HTTP only; lifespan and other scope types pass through;
      - while active, every path except `/health` gets 503 with `maintenance.html` rendered through `web.templates`, plus `Retry-After: 30` and `Cache-Control: no-store`;
      - otherwise it increments and decrements `in_flight` around the call (decrement in `finally`).
    - Constants `DRAIN_TIMEOUT_SECONDS=30` and `RETRY_AFTER_SECONDS=30`.
  - [x] 7.3 `templates/maintenance.html`: standalone `<html lang="pl">` with an inline `<style>` (`#16324f` heading, `#1f2937` text on `#f3f6fa`, system-ui) and no `/static` link or user context. Default variant: `Trwa odtwarzanie kopii zapasowej…` with two paragraphs and an `Odśwież stronę` link (`href=""`). A rollback-failed variant flag renders `Odtwarzanie nie powiodło się` and the restart text.
  - [x] 7.4 `create_app`: always set `app.state.maintenance = MaintenanceState()` and add `MaintenanceMiddleware`. Keep TrustedHost added last (outermost) in production.
  - [x] 7.5 `POST /restore` in `backup/router.py`, following "Web restore orchestration" steps 1–10:
    1. guards;
    2. lock (409);
    3. `new_operation_dir()`;
    4. `read_multipart_form` with the text allow-list, `backup_file`, `operation_dir/upload.marchive`, `max_file_bytes=service.max_bytes`, and a `before_file` that runs `verify_csrf` (403) before any file byte;
    5. map each `UploadError` to the catalogue (415/400/413);
    6. validate `confirm`, `file_size > 0` and the admin password;
    7. `run_in_threadpool(stage_restore)`, mapping `InvalidBackup`, `MissingAdministratorError` and `UnsupportedBackupRevisionError` to their 422 texts and `RestoreFailedError` to 500;
    8. `database.commit()`, `maintenance.enter()`, `wait_until_drained(30)` (on timeout: exit, discard, 500);
    9. `run_in_threadpool(apply_restore)`: `RestoreFailedError` exits maintenance and re-renders 500; `RestoreRollbackFailedError` keeps maintenance active and returns the standalone 500 variant;
    10. on success, exit maintenance and return a 303 to `/auth/login?restored=1` with `delete_cookie` using the same attributes as logout.
    - `finally`: `discard` (unless the journal keeps the dir) and release the lock.
    - Never log or echo passwords or the filename.
  - [x] 7.6 Ensure the restore UI tests pass
    - Run `py -3.12 -m pytest tests/backup -q`.
    - Verify the protected hashes.
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 6 new tests and the 4 extended parametrizations pass. The acceptance-6 roundtrip passes over HTTP.
- During the swap, everything except `/health` returns 503. CSRF is checked before any upload byte is written to disk.

---

### Task Group 8: Importable Preflight and Offline Restore CLI
**Dependencies:** 3, 5
**Estimated Steps:** 7
**Commit:** `feat: add importable preflight and offline restore cli`

- [x] 8.0 Complete preflight, the startup gate in `serve()` and the restore CLI
  - [x] 8.1 Write 6 focused tests
    - New `tests/test_release_guards.py`:
      - local `settings_factory` fixture (`Settings(data_dir=tmp_path, **overrides)`);
      - local `ai_preflight` fixture (`Preflight(settings=<testing settings on the migrated auth_engine DB>, fetch_model_ids=lambda: [])`);
      - `test_production_start_refuses_missing_tls_configuration(settings_factory)` (as written in plan 4; it may already be GREEN from Group 2, so record it as a characterization test);
      - `test_preflight_reports_missing_local_ai_without_hiding_the_degraded_state(ai_preflight)` (status `degraded`; `missing_components` includes `embedding` and `generation`);
      - `test_preflight_fails_when_sqlite_vec_is_unavailable` (monkeypatch `load_sqlite_vec` to raise `VectorExtensionUnavailableError`; status `failed`);
      - `test_preflight_main_strict_mode_fails_on_degraded` (`main([])` returns 0 when degraded and `main(["--strict"])` returns 1; monkeypatch the default fetcher to raise `OSError`);
      - `test_serve_refuses_production_start_when_preflight_fails` (monkeypatch `Preflight.check` to return `failed`; `SystemExit(1)`; `uvicorn.run` never called; missing components printed).
    - New `tests/backup/test_restore_cli.py`: `test_restore_cli_requires_typed_confirmation_and_restores_backup`. Monkeypatch `input` to give `nope` (exit 1, live data unchanged), then `RESTORE` with a monkeypatched `getpass`. The data is restored and `Restore completed. All sessions were signed out.` is printed. Use env vars for the strict Settings (secret, `DATABASE_URL`, `DATA_DIR`).
    - **Confirm RED**.
  - [x] 8.2 `src/medical_archive/preflight.py`
    - Frozen `PreflightCheck` and `PreflightResult`.
    - `Preflight(*, settings, fetch_model_ids=None)`. Move the probes from `tools/check_local_ai.py`: Python 3.12, `load_sqlite_vec` in memory, DB file exists with revision == head, `data_dir` writable, TLS readable (production only), PyMuPDF import, `shutil.which("tesseract")`, `pol` in `--list-langs`, and the embedding/chat model ids.
    - The default fetcher does a no-redirect GET `{base}/models` with `embedding_timeout_seconds` and sends no document data. `OSError` or `ValueError` means LM Studio is unavailable.
    - `check()`: status precedence failed > degraded > ok, and `missing_components` in table order.
    - `main(arguments) -> int` with `--strict`, using `Settings(testing=True)`, plus `if __name__ == "__main__"`.
  - [x] 8.3 `tools/check_local_ai.py` becomes a thin wrapper: `raise SystemExit(preflight.main(["--strict"]))`. Drop its `from __future__ import annotations`, since it is rewritten.
  - [x] 8.4 `__main__.serve()`: after strict Settings, run `Preflight(settings=settings).check()`. In production, `failed` prints the missing components and raises `SystemExit(1)`. In development, print warnings and continue. Keep the Group 3 serve test green (inject a passing preflight there via monkeypatch if needed).
  - [x] 8.5 `src/medical_archive/backup/restore.py` CLI, `main(arguments=None)`, modelled on `auth/bootstrap.py`:
    1. argparse `--backup-file`;
    2. strict `Settings()`, then `recover_backup_state`;
    3. English warning;
    4. `input()` must equal `RESTORE`, otherwise `SystemExit(1)`;
    5. `getpass` for the backup password;
    6. `backup_operation_lock`, copy the file into `new_operation_dir()`, `stage_restore`, `apply_restore` (no-op dispose);
    7. success message.
    - `InvalidBackup` subclasses map to English equivalents of the 422 texts and `RestoreFailedError` to the rolled-back message, all with exit code 1. No admin password is asked.
  - [x] 8.6 `scripts/restore-backup.ps1`: `param([Parameter(Mandatory)][string]$BackupFile)`. Fails when `Test-Path` is false, runs `python -m medical_archive.backup.restore --backup-file $BackupFile` and `exit $LASTEXITCODE`.
  - [x] 8.7 Ensure the preflight and CLI tests pass
    - Run the 6 new tests plus `tests/test_network_config.py`.
    - Parse-check `scripts/restore-backup.ps1`.
    - Smoke run: `py -3.12 tools/check_local_ai.py` (exit 1 is acceptable when LM Studio is down).
    - Run the quality gates and commit.

**Acceptance Criteria:**
- The 6 tests pass. A production start stops on `failed`, and degraded components are reported explicitly, never hidden.
- The offline restore needs the typed `RESTORE` confirmation and goes through the same staged, verified, rollback-safe service.

---

### Task Group 9: Release Checks and Polish Runbook
**Dependencies:** 3, 7, 8
**Estimated Steps:** 5
**Tests:** 0 new pytest tests. This is a scripts and docs group. Its "RED" is the missing release gate, and it is verified by running the gate.
**Commit:** `docs: add local deployment release checks`

- [x] 9.0 Complete the release gate and the README runbook
  - [x] 9.1 Define the verification first: run `.\scripts\check-release.ps1` and **confirm RED** (script missing). Write down the manual checklist that comes from spec Success Criteria 7 and 8.
  - [x] 9.2 `scripts/check-release.ps1`, with ordered steps that each abort with the step name and exit 1 on a non-zero `$LASTEXITCODE`:
    1. `python -m ruff check .`
    2. `python -m ruff format --check .`
    3. `python -m pytest -q`
    4. Alembic `upgrade head` → `downgrade base` → `upgrade head` against a new temp dir under `[IO.Path]::GetTempPath()`. `MEDICAL_ARCHIVE_DATABASE_URL` is set only for these commands and restored afterwards; the temp dir is removed in `finally`.
    5. `python -m medical_archive.preflight` (degraded = warning, failed = fail).
    6. `git status --porcelain --untracked-files=all` lines matching `\.(db|sqlite|sqlite3|db-wal|db-shm|marchive|pem|key|crt|cer|p12|pfx)$` or `\.env$` fail the gate.
    7. Print `Release checks passed`.
  - [x] 9.3 README runbook in Polish. Replace the "nie jest gotowa…" warning with the spec's 12 sections:
    - development start;
    - LAN HTTPS with mkcert (`mkcert -install`, `mkcert 192.168.1.10 archiwum.lan`), key storage, client CA install (Windows/Android/iOS), production variables, `run-lan.ps1`;
    - Windows firewall, Private profile only;
    - LM Studio on localhost, with the `Get-NetTCPConnection`/`netstat` check;
    - backup (scope, cross-profile warning, password rules, keep the file and the password apart, always back up before an update);
    - update procedure;
    - restore through the UI;
    - offline restore through the CLI (including automatic recovery at startup);
    - backup verification drill;
    - lost backup password or lost admin password;
    - network isolation check;
    - limitations (no secure delete, BitLocker, 1 GiB cap, about 3× free space).
    - Use placeholders only (`192.168.1.10`, `archiwum.lan`). No real addresses or secrets.
  - [x] 9.4 Parse-check all three scripts with `[System.Management.Automation.Language.Parser]::ParseFile`. Run `.\scripts\check-release.ps1` end to end; it must exit 0 (a degraded preflight warning is acceptable). Check that `git status --short` shows no DB, backup, certificate, key or `.env` files.
  - [x] 9.5 Manual drills, recorded in the work log:
    - synthetic-data restore on a separate `data_dir` (plan Task 4, step 5): export, mutate, restore via the UI and the CLI;
    - LAN and phone HTTPS check, outside-LAN port closed, LM Studio loopback-only. These are manual, owner-performed checks; record the outcome or "pending owner".
    - Then commit.

**Acceptance Criteria:**
- `check-release.ps1` exits 0 on the dev machine, and each failing step aborts with its name.
- The README is Polish, complete per the 12-section outline, and consistent with `python -m medical_archive`, the `sqlite-vec` runtime dependency and the scripts.

---

### Task Group 10: Test Review & Gap Analysis
**Dependencies:** 1, 2, 3, 4, 5, 6, 7, 8, 9
**Estimated Steps:** 5
**Commit:** `test: cover backup recovery and network config edge cases`

- [x] 10.0 Review and fill critical gaps
  - [x] 10.1 Review the tests from Groups 1–8 (45 new functions plus the 8 protected ones) against the spec's Core Requirements 1–17 and the error catalogue.
  - [x] 10.2 Analyze gaps for THIS feature only. Likely candidates, in priority order:
    - startup recovery of a `committed` journal (cleanup only, no rollback);
    - an invalid or unreadable journal makes `create_app(testing=False)` fail with `RestoreFailedError`;
    - stale `.backup-tmp` and `.restore-staging` entries are removed at startup;
    - a failed rollback keeps maintenance active and renders the standalone 500 variant;
    - export `BackupSourceError` gives a 500 page with no partial download;
    - export over the cap gives 413;
    - multipart client disconnect or malformed body gives 400 and the partial file is removed;
    - TLS pairing (only one of cert or key set) is rejected in development too;
    - `encrypt` over `MAX_ENVELOPE_BYTES` raises `ValueError`;
    - a manifest with an absolute path or extra keys is rejected.
  - [x] 10.3 Write up to 10 additional strategic tests, behaviour-named and module-level, using fakes and monkeypatch only (no mock library).
  - [x] 10.4 Run the feature-specific tests only: `py -3.12 -m pytest tests/backup tests/test_network_config.py tests/test_release_guards.py tests/documents/test_multipart_form.py tests/documents/test_upload.py -q` (expect about 53–63 functions). Then run the full quality gates.
  - [x] 10.5 Verify that the protected file hashes are unchanged, then commit.

**Acceptance Criteria:**
- All feature tests pass, with no more than 10 additional tests.
- The full suite, `ruff check` and `ruff format --check` are clean.

---

## Execution Order

1. Group 1: Encrypted Backup Format (6 steps)
2. Group 2: Fail-closed Settings (7 steps)
3. Group 3: Strict App Startup and TLS Serving (7 steps, depends on 2)
4. Group 4: Streaming Multipart Form Parser (5 steps; independent, run after 3)
5. Group 5: Verified Restore Service (10 steps, depends on 1 and 3)
6. Group 6: Backup Page, Export and Login Notice (7 steps, depends on 4 and 5)
7. Group 7: Web Restore Flow and Maintenance Mode (6 steps, depends on 6)
8. Group 8: Preflight and Offline Restore CLI (7 steps, depends on 3 and 5)
9. Group 9: Release Checks and Polish Runbook (5 steps, depends on 3, 7 and 8)
10. Group 10: Test Review & Gap Analysis (5 steps, depends on all)

Key dependency notes:
- The module-level `app` is removed in Group 3, and the README start command is updated in the same group.
- `env.py` URL precedence lands in Group 5, together with the test that needs it.
- `create_app` grows step by step: strict Settings and TrustedHost (3), then recovery and `BackupService` (5), then the router (6), then the maintenance middleware (7).

## Standards Compliance

Follow the standards in `.maister/docs/standards/`:
- **global/**: coding-style (absolute imports, keyword-only options, frozen dataclasses, Ruff 100), conventions (quality gates, domain package layout, routers wired only in `create_app`, one Conventional Commit per group, never commit secrets, DBs or backups), error-handling (typed `*Error`, `raise … from error` at the router, no silent fallbacks, cleanup in `finally`), minimal-implementation (no offline export, no KDF injection, no stubs), validation (allow-lists, server-side).
- **security/**: authentication (CSRF on both POSTs, checked before the restore file streams; rotation on render; `hmac.compare_digest`; strict form parsing; cookie deletion after restore), access-control (`require_admin` server-side; the nav link is not access control), data-protection (event-style logs with opaque ids; streamed uploads; plaintext only under `data_dir` and deleted; LAN-only HTTPS; loopback LM Studio; no CORS).
- **backend/**: configuration (single `Settings`, bounded fields, fail-closed production), api (`Annotated[..., Depends]`, `app.state` collaborators, `include_in_schema=False`, 303 after POST, OpenAPI disabled), migrations (no new migration; programmatic URL precedence documented in `env.py`).
- **frontend/**: components (`base.html` + `section.panel`, standalone `lang="pl"` maintenance page), accessibility (`<label for>`, one `role=alert`, `aria-invalid`/`aria-describedby`, `aria-current`), css (local `app.css`, rem, no external URLs), responsive (file input `max-width:100%`).
- **testing/**: test-first with confirmed RED, behaviour-named module-level tests, `Fake*` and monkeypatch (no mock library), Alembic-migrated `tmp_path` SQLite, synthetic data, required negative backup cases.

## Notes

- **Test-driven.** Each group starts with 2–8 tests and confirms RED. In Group 1 the protected existing tests are the RED tests for crypto and roundtrip.
- **Run incrementally.** Run the group's tests plus the named regression gates, then the mandatory project quality gates before each commit.
- **Mark progress.** Check off steps as they are completed. This file is the resume source of truth.
- **Reuse first:**
  - `require_admin`, `verify_csrf`, `rotate_csrf`, `validate_password` and `verify_password` from auth;
  - `render_template` and `templates`;
  - the `write_document` atomic pattern;
  - the `read_multipart_file` state machine (extracted, not duplicated);
  - `load_sqlite_vec`;
  - the bootstrap CLI pattern;
  - the probes in `tools/check_local_ai.py`;
  - the conftest fixtures (`auth_engine`, `app`, `client`, `admin_user`, `member_user`, `extract_csrf_token`).
- **Windows specifics.** Close every sqlite3 connection and file handle before any directory rename. Dispose the engine (`auth_engine.dispose` in tests, `reset_database_engine` in production) before the backup-API write.
- **Risk hot spots:**
  - Group 5 (journal and rollback ordering): rollback and crash tests come first;
  - Group 4 (the shared parser must keep `test_upload.py` green);
  - Group 7 (drain and commit ordering before maintenance).
- **Task tracking.** The `TaskCreate`/`TaskUpdate` tools were not available in this planner session. The orchestrator should create the 10 group items with the dependency chain listed above (2→3; 1,3→5; 4,5→6; 6→7; 3,5→8; 3,7,8→9; all→10).
