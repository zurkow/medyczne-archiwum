# Requirements — Plan 4: encrypted backup/restore, TLS and LAN-only bind

## Initial description
Implement plan 4 (`docs/superpowers/plans/2026-10-13-backup-i-lan.md`): encrypted backup format AES-256-GCM + scrypt with
roundtrip, safe non-destructive restore + admin backup UI, TLS and LAN-only bind with fail-closed production config,
release checks (`scripts/check-release.ps1`) and restore instructions. Existing untracked tests
`tests/backup/test_crypto.py` and `tests/backup/test_roundtrip.py` are the source of truth for the envelope/format API.

## Q&A (Phase 5)
- **User journey** — Confirmed: only the household admin uses `/settings/backup`, reached via the new admin-only
  "Kopia zapasowa" nav link (after "Zaproszenia"); members get 403 if they try the URL; the offline CLI
  (`scripts/restore-backup.ps1` → `python -m medical_archive.backup.restore`) is for the admin on the host with the server
  stopped, when the web UI is unusable.
- **Code reuse** — Confirmed:
  - `auth/router.py` invitation page pattern (admin GET with `rotate_csrf` + commit; POST with CSRF check).
  - `documents/storage.py` atomic write (temp + fsync + `os.replace`) and the streaming multipart parser, extended to a
    text-field allow-list + one file streamed to disk with a configurable cap (document upload behaviour unchanged).
  - `auth/bootstrap.py` (argparse + getpass + strict `Settings`) as the template for the restore CLI.
  - `tools/check_local_ai.py` probe logic moves into importable `src/medical_archive/preflight.py`; the tool becomes a
    thin wrapper.
- **Visual assets** — None provided beyond `analysis/ui-mockups.md` (no `analysis/visuals/`).
- **TLS** — mkcert local CA installed on client devices; README uses generic placeholders (e.g. `192.168.1.10`,
  `archiwum.lan`); no silent self-signed generation; `run-lan.ps1` fails if cert/key are missing.

## Decisions carried from earlier phases
See `analysis/clarifications.md` and `analysis/scope-clarifications.md` (15 decisions). Summary:
own streaming multipart parser; one-shot v1 envelope capped at 1 GiB (export and restore); wipe all sessions after
restore; offline CLI restore; restore via SQLite online-backup-API copy into the live DB + `documents/` dir rename +
pre-restore snapshot + startup restore journal; older known alembic revisions upgraded on the staged copy, newer/unknown
rejected; `python -m medical_archive` `serve()` replaces module-level `app`; `environment=development` default with
loopback-only host; TrustedHost from host + optional `allowed_hosts` (production); maintenance 503 for all but `/health`;
backup password via `validate_password` + confirmation; sqlite-vec required in production; reject backups without an
active admin; temp/staging under `data_dir`; importable preflight ok/degraded/failed; UI-only export; process-wide lock
→ 409; Polish README runbook.

## Functional requirements summary
1. **Envelope** (`backup/crypto.py`): `encrypt(payload, password)` / `decrypt(envelope, password)`; header
   `MARCHIVE | version=1 | salt_len=16 | nonce_len=12 | salt | nonce | ciphertext+tag`; header authenticated as AAD;
   scrypt N=2**15 r=8 p=1 len 32, maxmem 64 MiB; header/length/size checks before KDF; `MAX_ENVELOPE_BYTES` read at
   call time; `InvalidBackupPassword(InvalidBackup)`.
2. **Archive format** (`backup/format.py`): ZIP with `MANIFEST_NAME`, `DATABASE_NAME`, `files/<storage_key>`; manifest
   keys `archive_id`, `format_version`, `schema_revision`, `storage_keys` (+ per-file size/sha256 allowed); strict
   `validate_archive`: no absolute/`..`/backslash/drive/duplicate/unlisted/missing entries, size limits,
   `MAX_MANIFEST_BYTES` read at call time; no absolute paths in manifest.
3. **Export** (`backup/service.py`): `create_backup(destination, password, *, data_dir, database_url,
   confirmed_all_profiles=False)`; SQLite online backup API snapshot (WAL content included); keys enumerated from the
   snapshot; private temp under `data_dir`, deleted in `finally`; `BackupConfirmationRequired`.
4. **Restore**: two-phase (decrypt + validate fully into staging, incl. integrity_check, foreign_key_check, alembic
   revision handling, active admin present, vec0 check) then maintenance + lock, engine dispose/reset, pre-restore
   snapshot, backup-API copy, documents swap, session wipe, health check, rollback on failure; startup journal recovery.
5. **UI/router** (`backup/router.py`, `templates/backup.html`, `templates/maintenance.html`): GET page, POST export
   (attachment, `Cache-Control: no-store`, file removed after send), POST restore (multipart, 422 invalid, 413 too large,
   409 busy, 403 member/CSRF/wrong admin password), 303 → `/auth/login?restored=1` with fixed notice.
6. **CLI**: `python -m medical_archive.backup.restore` + `scripts/restore-backup.ps1`.
7. **Network/config**: `Settings` fields `environment`, `host`, `port`, `tls_cert`, `tls_key`, `allowed_hosts`,
   backup size cap; LM Studio loopback validator; production model validator (TLS files, secret, private LAN host, not
   0.0.0.0/loopback/multicast/link-local/public); `create_app(testing=False)` uses strict Settings; TrustedHost in
   production; no CORS; docs off; `python -m medical_archive` serve with TLS; `scripts/run-lan.ps1`.
8. **Release**: `src/medical_archive/preflight.py` (injectable probes, ok/degraded/failed, missing_components);
   `scripts/check-release.ps1` (ruff, format, pytest, alembic up/down/up on temp DB, preflight); README runbook (Polish).
9. **Tests**: existing two backup test files pass unmodified; new `test_restore_safety.py`, `test_network_config.py`,
   `test_release_guards.py` plus negative cases (member 403, CSRF, wrong admin password, missing confirmation, size cap,
   busy lock, no-admin backup, newer revision, rollback on failure, no passwords in logs).

## Reusability opportunities
`require_admin`, `get_authenticated_session`, `rotate_csrf`/`verify_csrf`, `verify_password`, `validate_password`,
`render_template`, `write_document` atomic pattern, `load_sqlite_vec`, `create_database`, conftest fixtures
(`auth_engine`, `admin_user`, `member_user`, `client`, `extract_csrf_token`).

## Scope boundaries
In: everything in plan 4 Tasks 1–4 plus the restore CLI. Out: offline export CLI, chunked/streamed envelope,
python-multipart, automatic certificate generation, scheduled backups, remote/cloud backup targets.

## Technical considerations
Windows file locking (dispose engine, drain in-flight requests); `lru_cache` engine reset; tests point database_url at
`tmp_path/auth-test.db` and need injectable engine/DB path via `app.state`; cheap KDF parameters injectable in tests
while production asserts the constants; sqlite-vec needed for migration 0004 and vec0 validation.
