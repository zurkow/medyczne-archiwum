# Scope Clarifications (Phase 2) — 2026-10-14

## Critical decisions
1. **Restore swap mechanism** — Under a maintenance lock, copy the validated staged DB into the live DB with the SQLite
   online backup API (page-by-page, same DB path); swap `documents/` by directory rename; take a pre-restore snapshot for
   rollback; a startup restore journal finishes or rolls back an interrupted restore.
2. **Older schema revisions** — Accept backups with an older *known* alembic revision by running `alembic upgrade head`
   on the staged copy before validation; reject newer/unknown revisions.
3. **Startup entry point** — Remove module-level `app = create_app()`; add `python -m medical_archive` (`serve()`) that
   builds strict `Settings` and runs uvicorn with host/port/TLS (`proxy_headers=False`, `server_header=False`);
   `scripts/run-lan.ps1` wraps it; README start command updated.

## Important decisions (all recommended defaults accepted)
4. `environment` defaults to `development` with loopback-only host; LAN host allowed only in production.
5. `TrustedHostMiddleware` from `host` + optional `allowed_hosts` (mkcert hostname), production only, wildcard rejected.
6. Maintenance mode: everything except `/health` returns 503 during restore.
7. Same configurable 1 GiB cap on export as on restore; estimate before building and fail early; originals ZIP_STORED.
8. Backup password: reuse `validate_password` (12–1024 chars) + repeat-confirmation field.
9. sqlite-vec required in production (preflight fails without it); restore validates `rag_chunk_vectors`.
10. Reject backups with no active admin; UI warns that accounts/credentials come from the backup.
11. Plaintext temp/staging/rollback under `data_dir/.backup-tmp` and `data_dir/.restore-staging` (same volume),
    cleaned at startup and in `finally`; README notes Windows has no secure delete.
12. Importable `src/medical_archive/preflight.py` with injectable probes, statuses ok/degraded/failed
    (degraded: OCR/models; failed: sqlite-vec/DB/TLS/data_dir), reused by `check-release.ps1`.
13. Export is UI-only (no offline export CLI).
14. Process-wide backup/restore lock; concurrent attempt → 409 "Operacja kopii zapasowej już trwa."
15. README runbook in Polish: mkcert/CA install, firewall Private profile, LM Studio localhost, backup before update,
    restore via UI and CLI, lost backup password (unrecoverable), lost admin password (bootstrap), isolation check.

## Carried from Phase 1
- Own streaming multipart parser (text-field allow-list + one file); one-shot v1 envelope, 1 GiB cap;
  wipe all sessions after restore; offline CLI restore behind `scripts/restore-backup.ps1`.
