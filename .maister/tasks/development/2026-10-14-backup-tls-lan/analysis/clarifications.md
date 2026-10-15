# Clarifications (Phase 1) — 2026-10-14

1. **Restore upload parsing** — Extend the project's own streaming multipart parser: fixed allow-list of text fields
   (csrf_token, account password, backup password, confirmation) + exactly one file, streamed to a private temp file
   under data_dir with a configurable cap. No `python-multipart` dependency.
2. **Backup size / format** — Keep the one-shot v1 envelope that the existing tests define
   (MARCHIVE | version | salt_len | nonce_len | salt(16) | nonce(12) | ct+tag). Reject envelopes > 1 GiB (configurable cap).
3. **Sessions after restore** — Wipe all login sessions after a successful restore; everyone (incl. the admin) logs in again.
4. **scripts/restore-backup.ps1** — Implement an offline CLI restore (server stopped), e.g.
   `python -m medical_archive.backup.restore`, as the recovery path when the UI is unusable.

Source of truth where plan text and existing tests differ: the existing tests in `tests/backup/`
(extra nonce-length header byte; InvalidBackupPassword subclasses InvalidBackup; MAX_* limits read at call time).
