# Reality Check: Plan 4 (encrypted backup/restore, TLS + LAN, release checks)

Date: 2026-10-15. Branch `feat/plan-4-backup-tls`, commits 6e4148e..959beba.
Assessor: reality-assessor (read-only; synthetic data in scratch directories only).

## Status: ⚠️ Issues Found. Conditional GO for the household LAN deployment

The backup and restore path works end to end on synthetic data, including the failure cases. The production configuration fails closed. The release gate passes. No critical code gaps were found. Three things stand between this and "done":
1. The owner's manual LAN/phone/TLS drills. No real TLS handshake has been exercised.
2. Two medium operational gaps: nothing stops the CLI restore while the server runs, and the new-machine restore path is unclear.
3. A handful of low-severity residual risks.

## What was verified independently (evidence)

| Claim | Reality | Evidence |
|---|---|---|
| Test suite green | Confirmed | `py -3.12 -m pytest -q`: 206 passed, 57 s |
| Release gate works | Confirmed | `scripts/check-release.ps1` (scratch DB/data dir): ruff, format, pytest 206, alembic up/down/up, preflight `degraded` (only tesseract/ocr-pol missing; LM Studio models found on loopback), git-status guard. Output: `Release checks passed`, exit 0 |
| Web export produces encrypted file | Confirmed | Real `create_app(settings=...)` (non-testing path, startup recovery wired) via TestClient over https: export 200, attachment `medyczne-archiwum-YYYYMMDD-HHMMSS.marchive`, magic `MARCHIVE`, no plaintext (file bytes/e-mail) in envelope, `.backup-tmp` empty afterwards |
| Wrong password / corruption never touches live data | Confirmed | Wrong password: 422. Flipped byte: 422. Live DB rows and files unchanged in both cases |
| Restore replaces DB + originals, wipes sessions | Confirmed | 303 to `/auth/login?restored=1`. Post-backup document row and file removed. Tampered original restored byte-exact. Old cookie rejected (303). Re-login works. `/documents` lists the restored doc. No staging dir or journal left; maintenance flag cleared |
| Browser field order (csrf, file, passwords, confirm) | Confirmed | Raw multipart in DOM order: 303 restore success |
| Offline CLI restore | Confirmed (onto migrated DB) | `restore.main` with typed `RESTORE`: exit 0, data restored. Wrong password: exit 1, data unchanged |
| Startup recovery of interrupted restore | Confirmed | Simulated power loss (KeyboardInterrupt after the live `documents` rename, journal `database_replaced`). Next `create_app` rolled the DB back to the pre-restore state and moved the live documents back. Journal and staging were removed |
| Rollback on swap failure | Confirmed | A file in `documents` held open (Windows): PermissionError, rolled back, "current data was not changed", live state intact |
| Staged upgrade can't hit live DB | Confirmed by code | `migrations/env.py` now prefers the code-set `sqlalchemy.url` over the env var; `alembic.ini` URL empty |
| Fail-closed production config | Confirmed | Rejected: `0.0.0.0`, `::`, `127.0.0.1`, `8.8.8.8`, `100.64.1.1`, `169.254.1.1`, missing cert, missing key, missing secret, LAN host in development. Accepted: 192.168/10/172.16, `fd00::1` |
| Host-header pinning | Confirmed | Production app: `192.168.1.10:8443` and `archiwum.lan:8443` give 200; `evil.example` and `127.0.0.1` give 400 |
| LM Studio loopback-only | Confirmed locally | Config rejects non-loopback URLs. `Get-NetTCPConnection` shows only `127.0.0.1:1234` listening. LAN-device check still pending |
| Polish runbook | Present | README sections 2–12 (TLS/mkcert, firewall, LM Studio, backup, update, UI/CLI restore, verification drill, lost passwords, isolation checks, limits) |

Not verified (by constraint): real uvicorn TLS handshake on a LAN IP, phone trust of the mkcert CA, firewall rule behaviour, port closed from outside the LAN, LM Studio unreachable from a LAN device.

## Critical gaps

None in code.

## Warnings (High / Medium)

### W1. LAN/TLS behaviour is unproven in reality (High, pending owner)
- **Location**: `scripts/run-lan.ps1`, `__main__.serve`, README §2/§3/§11.
- **Reality**: TLS is only checked through config validation and TestClient. No real handshake ran, and no phone, firewall or outside-LAN test ran.
- **Fixable**: yes (owner drill, no code).
- **Suggestion**: run the README §11 drill: phone opens `https://<ip>:8443` with no warning, port closed on mobile data, `1234` closed from the LAN. Record the results in work-log before calling plan 4 done.

### W2. The CLI restore does not enforce "server stopped" (Medium)
- **Location**: `src/medical_archive/backup/restore.py` (`WARNING` text only). `_OPERATION_LOCK` is a per-process `threading.Lock`.
- **Evidence**: with another process holding an open SQLAlchemy connection to the live DB, the CLI restore completed (exit 0) and swapped the DB underneath it. With a file open in `documents`, it rolled back safely.
- **Impact**: if the owner runs the CLI with the server up, the server keeps serving on swapped data without maintenance mode. A concurrent write could land in the restored DB. Requests in flight could hit the window between the DB swap and the documents swap.
- **Fixable**: yes.
- **Suggestion**: have the server hold an OS-level lock file in `data_dir` (e.g. `msvcrt.locking` / exclusive open of `.server.lock`). The CLI refuses with a Polish/English message if it cannot take that lock.

### W3. Restoring onto a new or empty machine fails with a misleading message (Medium)
- **Location**: `service.apply_restore` → `_snapshot_live_database` → `BackupSourceError("live database file does not exist")`, mapped to `RESTORE_FAILED` in `restore.py`. README §8.
- **Evidence**: CLI on a fresh data dir with no DB printed "The restore failed and was rolled back. The current data was not changed." (exit 1). After `alembic upgrade head` it succeeded.
- **Impact**: the most likely real restore (disk died, new PC) needs an unstated step. §9 shows migrate+bootstrap for a test dir, but §8 does not say so for disaster recovery.
- **Fixable**: yes.
- **Suggestion**: add a "Nowy komputer / pusty katalog danych" subsection to §8: install, `python -m alembic upgrade head`, then `restore-backup.ps1`; bootstrap is not needed. Alternatively, let `apply_restore` treat a missing live DB as "nothing to snapshot". At minimum, give the CLI a specific message: "run alembic upgrade head first".

## Info / Low

| # | Severity | Item | Location | Fixable | Suggestion |
|---|---|---|---|---|---|
| L1 | Low | Long `data_dir` paths break backup/restore (Windows MAX_PATH). Staging adds about 110 chars (`.restore-staging\<uuid>\staged\documents\<uuid>`) and export about 75. Evidence: with TEMP set to a 116-char path, 21 backup tests failed with `sqlite3.OperationalError: unable to open database file`. Default `~\.medical-archive` is fine | `backup/service.py`, `backup/format.py` | yes | Preflight warning when `len(str(data_dir)) > ~140`, or a README §12 note |
| L2 | Low | A non-`RestoreFailedError` from `apply_restore` (in practice only `dispose_engine` or MemoryError) leaves maintenance on (503) until restart. Restart recovery handles the data | `backup/router.py` L264–270 | yes | `try/finally` or a broad `except Exception` that exits maintenance unless rollback failed |
| L3 | Low | Early `UploadError` (Content-Length 413/415/malformed) and the 409 busy path render the page (rotating CSRF) before CSRF is verified. Only reachable by an authenticated admin session; no data effect | `backup/router.py` L166–232 | yes | Verify CSRF from a header or reject before rendering; or accept as documented |
| L4 | Low | Peak RAM is about 2–3x the archive (≤1 GiB archive → ~2–3 GiB) during export and restore, next to LM Studio. The spec says so, the README does not | `crypto.encrypt/decrypt`, `_stage` | yes (doc) | Add a RAM note to README §12 |
| L5 | Low | No admin-password reset. Documented workaround: restore an older backup | README §10 | yes | Future `auth.reset_password` CLI (offline, server stopped) |
| L6 | Low | `tests/conftest.py` `app` fixture uses `Settings(testing=True)`, which creates `~/.medical-archive` if missing (observed: it exists, empty). The release gate runs pytest too | `tests/conftest.py` | yes | Set `data_dir=tmp_path` in the fixture |
| L7 | Info | Preflight degraded: tesseract not on PATH (OCR unavailable). Not caused by plan 4 | environment | yes | Install Tesseract + `pol`, or accept degraded |
| L8 | Info | `_is_private_lan_address` accepts 192.0.2.0/24, 198.18.0.0/15 and IPv4-mapped `::ffff:192.168.x.x` (Python `is_private`). Harmless: the address must be bound locally | `config.py` | yes | Optional explicit RFC1918/ULA allow-list |
| L9 | Info | `run-lan.ps1` and `restore-backup.ps1` call `python`, so the venv must be active (README §1). On this machine, `python` is global 3.12.10 | `scripts/*.ps1` | yes | Mention venv activation in §2/§8 or use `.venv\Scripts\python.exe` when present |
| L10 | Info | The UI restore checks the admin password only after the full upload (≤1 GiB) is streamed to disk. Needs an authenticated admin + CSRF | `backup/router.py` | n/a | Acceptable |

## Functional completeness

Roughly 90%. Every functional piece of the spec works in practice: crypto, archive validation, WAL-safe export, staged restore, rollback, journal recovery, maintenance, UI, CLI, fail-closed config, TrustedHost, preflight, release gate and runbook. The rest is real-world network verification (W1) and two operational guardrails (W2, W3).

## Pragmatic action plan

| # | Priority | Task | Success criterion | Effort |
|---|---|---|---|---|
| 1 | High | Owner runs README §11 drill (phone HTTPS, port closed off-LAN, LM Studio unreachable from LAN) | Results recorded in work-log, all three pass | 30–45 min |
| 2 | High | First real backup + restore drill on a test data dir (README §9) with the real data | Restored state matches; the backup file and password are stored separately | 20 min |
| 3 | Medium | Document the new-machine restore (migrate first) in README §8, and/or a specific CLI message for a missing live DB | A fresh-dir CLI restore either works or says exactly what to run | 15–45 min |
| 4 | Medium | Cross-process server lock checked by the CLI restore | CLI refuses while the server runs; test covers it | 1–2 h |
| 5 | Low | Exit maintenance on any unexpected `apply_restore` exception (except rollback-failed) | Unit test with a raising `dispose_engine` shows maintenance cleared | 20 min |
| 6 | Low | README §12: RAM ~2–3x archive and a short `data_dir` path; optional preflight path-length warning | Notes present | 15 min |
| 7 | Low | Point the `app` test fixture at `tmp_path` | pytest no longer creates `~/.medical-archive` | 10 min |

## Deployment decision

**GO for the household LAN deployment, on two conditions**: items 1 and 2 above pass, and the owner follows the documented rule of stopping the server before the CLI restore (W2) until item 4 lands. The code does what plan 4 promises: a bad archive or wrong password never overwrites live data, an interrupted restore recovers at startup, and the app refuses to start in production without TLS on a private LAN address.
