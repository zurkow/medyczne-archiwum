# Work Log

## 2026-10-14 - Implementation Started

**Total Steps**: 65
**Task Groups**: 1 Encrypted Backup Format · 2 Fail-closed Settings · 3 Strict App Startup and TLS Serving ·
4 Streaming Multipart Form Parser · 5 Verified Restore Service · 6 Backup Page, Export and Login Notice ·
7 Web Restore Flow and Maintenance Mode · 8 Preflight and Offline Restore CLI · 9 Release Checks and Polish Runbook ·
10 Test Review & Gap Analysis
**Branch**: feat/plan-4-backup-tls (one scoped Conventional Commit per group)
**Note**: TaskCreate/TaskUpdate tools are unavailable in this session; progress is tracked via plan checkboxes and this log.

## Standards Reading Log

### Loaded Per Group
(Entries added as groups execute)

### Group 1: Encrypted Backup Format
**From Implementation Plan**: global/coding-style, global/error-handling, global/conventions, global/minimal-implementation, security/data-protection, testing/test-writing
**From INDEX.md**: global/validation (allow-lists), backend/models (UUID4 storage keys)
**Discovered During Execution**: security/authentication (step 1.5 — reuse validate_password for backup password)

## 2026-10-14 - Group 1 Complete

**Steps**: 1.1 through 1.6 completed
**Tests**: 23 group tests passed (12 existing protected cases + 11 new); full suite 111 passed; ruff check + format clean
**RED**: 3 collection errors, ModuleNotFoundError medical_archive.backup
**Files Modified**: src/medical_archive/backup/{__init__,crypto,format,service}.py (created), tests/backup/test_archive_format.py (created), tests/backup/test_crypto.py + test_roundtrip.py (committed unchanged), pyproject.toml, .gitignore, README.md
**Commit**: 6e4148e feat: add encrypted backup format
**Notes**:
- Deviation: added `[tool.ruff.lint.per-file-ignores] "tests/backup/test_roundtrip.py" = ["F401", "I001"]` because the protected file must stay byte-identical (accepted by orchestrator).
- validate_archive rejects entries whose `orig_filename != filename` (Windows backslash normalisation).
- Extra wrapped errors: EOFError, RecursionError, zlib.error; duplicate JSON keys rejected. New helper `format.is_storage_key`.
- Follow-up for Group 5/6: raw OSError from write_archive (file vanished after estimate) should map to the 500 source-error page.
- Protected-file hashes: test_crypto 95A57945…3156, test_roundtrip F9844745…62D4.

### Group 2: Fail-closed Settings
**From Implementation Plan**: backend/configuration, security/data-protection, global/validation, global/coding-style, global/error-handling, testing/test-writing
**From INDEX.md**: global/minimal-implementation (local _MIB/_GIB constants, no config→backup dependency)
**Discovered During Execution**: none

## 2026-10-14 - Group 2 Complete

**Steps**: 2.1 through 2.7 completed
**Tests**: 19 new cases (6 functions) + test_database regression = 22 passed; full suite 130 passed; ruff clean; protected hashes unchanged
**RED**: 17× DID NOT RAISE (extra="ignore"), 1× AttributeError environment, 1× __init__ ValueError instead of ValidationError
**Files Modified**: src/medical_archive/config.py, tests/conftest.py (autouse env isolation), tests/test_network_config.py (created)
**Commit**: 8fa058a feat: add fail-closed production settings
**Notes**: host stored normalised (usable directly by uvicorn/TrustedHost in Group 3); CGNAT 100.64/10 and link-local rejected; production test uses data_dir=tmp_path and asserts all four problems in one ValidationError.

### Group 3: Strict App Startup and TLS Serving
**From Implementation Plan**: backend/configuration, backend/api, security/data-protection, global/conventions, global/coding-style, global/error-handling, testing/test-writing
**From INDEX.md**: global/minimal-implementation (preflight gate deferred to Group 8)
**Discovered During Execution**: none

## 2026-10-14 - Group 3 Complete

**Steps**: 3.1 through 3.7 completed
**Tests**: 4 new; group run 27 passed; full suite 134 passed; ruff clean; protected hashes unchanged
**RED**: ModuleNotFoundError medical_archive.__main__ (3), AttributeError reset_database_engine (1)
**Files Modified**: src/medical_archive/{db,main}.py, src/medical_archive/__main__.py (created), scripts/run-lan.ps1 (created, UTF-8 BOM), README.md, tests/test_network_config.py, tests/test_database.py
**Commit**: b406f5e feat: serve app over trusted lan tls
**Notes**: create_app(*, testing=False, settings=None) fails closed; TrustedHost outermost in production; module-level app removed; run-lan.ps1 fails closed on missing cert/key/secret (smoke-tested in pwsh 7 and PS 5.1, no server started). IPv6 TrustedHost untested (known limitation).

### Group 4: Streaming Multipart Form Parser
**From Implementation Plan**: security/authentication, security/data-protection, global/coding-style, global/error-handling, global/minimal-implementation, testing/test-writing
**From INDEX.md**: global/validation (field allow-list, early Content-Length check)
**Discovered During Execution**: global/conventions (CRLF working tree)

## 2026-10-14 - Group 4 Complete

**Steps**: 4.1 through 4.5 completed
**Tests**: 5 functions (7 cases) new; group + regression 24 passed; full suite 141 passed; test_upload.py unchanged and green; ruff clean; protected hashes unchanged
**RED**: ImportError MultipartForm / read_multipart_form
**Files Modified**: src/medical_archive/documents/storage.py, src/medical_archive/web/forms.py (created), src/medical_archive/auth/router.py, tests/documents/test_multipart_form.py (created)
**Commit**: 9dedfd8 feat: add streaming multipart form parser
**Notes for Groups 5–7**: oversized text field → 413 (auth standard); restore router must map UploadError 400/413 to the Polish catalogue (English detail strings listed in the Group 4 report); before_file hook exceptions propagate unchanged (use it for CSRF 403); FileExistsError on pre-existing destination is not wrapped — always use a fresh op dir.

### Group 5: Verified Restore Service
**From Implementation Plan**: security/data-protection, global/error-handling, backend/migrations, backend/models, security/access-control, global/coding-style, global/minimal-implementation, testing/test-writing
**From INDEX.md**: global/validation (journal key/state allow-list, UUID op ids)
**Discovered During Execution**: none

## 2026-10-14 - Group 5 Complete

**Steps**: 5.1 through 5.10 completed
**Tests**: 8 functions (12 cases) new; tests/backup 35 passed; full suite 153 passed; ruff clean; protected hashes unchanged
**RED**: ImportError BackupService (conftest load); mutation check on env.py precedence confirmed the upgrade test fails without the fix
**Files Modified**: migrations/env.py, src/medical_archive/backup/service.py, src/medical_archive/main.py, tests/backup/conftest.py (created), tests/backup/test_restore_safety.py (created)
**Commit**: ae93a21 feat: add verified backup restore
**Notes**:
- Restore-time alembic upgrade builds Config() without alembic.ini (fileConfig would disable app loggers).
- Swap-failure tests run the real _swap_documents before raising, exercising both DB and documents rollback; crash test also verifies stale .backup-tmp cleanup.
- Rollback refuses a missing source DB (no silent empty DB). Post-commit/rollback cleanup failures are logged (backup_restore_cleanup_failed) and left for startup recovery; startup sweep failures propagate.
- Extra log events: backup_restore_rejected code=…, backup_restore_failed code=… (codes only).
- Follow-ups: update .maister/docs/standards/backend/migrations.md line 30 (env.py precedence now code URL > env var > settings); Group 10 gap test candidate — invalid journal makes create_app(testing=False) fail with RestoreFailedError.

### Group 6: Backup Page, Export Route and Login Notice
**From Implementation Plan**: security/authentication, security/access-control, security/data-protection, backend/api, global/error-handling, global/coding-style, testing/test-writing
**From INDEX.md**: frontend/accessibility, frontend/components, frontend/css, global/conventions
**Discovered During Execution**: frontend/responsive (file input max-width), global/minimal-implementation (RESTORE_* constants kept per plan)

## 2026-10-14 - Group 6 Complete

**Steps**: 6.1 through 6.7 completed
**Tests**: 6 functions (11 cases) new; group + tests/auth 23 passed; full suite 164 passed; ruff clean; protected hashes unchanged
**RED**: 11 failed (404 on backup routes, missing role=status notice)
**Files Modified**: src/medical_archive/backup/router.py (created), templates/backup.html (created), templates/base.html, templates/login.html, auth/router.py, main.py, static/app.css, tests/backup/conftest.py, tests/backup/test_backup_ui.py (created)
**Commit**: d44241e feat: add admin backup page with encrypted export
**Notes**:
- Group 1 follow-up closed: raw OSError from write_archive → 500 source-error page; destination unlinked on every error path.
- Field error ids are `<field-id>-error`; CSS inserted before the existing media query (~90 lines expanded); CSRF failure returns existing JSON 403 detail.
- Lock covers create_backup only, released before FileResponse streams.
- Group 7 must extend the 4 parametrized tests with restore cases (member/anon, CSRF w/ empty .restore-staging, wrong admin pw / missing confirm / missing file, 409), add maintenance to backup_app, and delete the session cookie with logout attributes after restore.

### Group 7: Web Restore Flow and Maintenance Mode
**From Implementation Plan**: security/authentication, security/access-control, security/data-protection, global/error-handling, global/coding-style, global/minimal-implementation, backend/api, frontend/components, frontend/accessibility, testing/test-writing
**From INDEX.md**: frontend/css (inline style on maintenance page since /static is blocked), global/validation (multipart text allow-list)
**Discovered During Execution**: none

## 2026-10-14 - Group 7 Complete

**Steps**: 7.1 through 7.6 completed
**Tests**: 5 new functions + 4 extended parametrizations; tests/backup 58 passed; full suite 176 passed; ruff clean; protected hashes unchanged
**RED**: ModuleNotFoundError backup.maintenance; 11 restore cases failing (404 / no app.state.maintenance)
**Files Modified**: src/medical_archive/backup/maintenance.py (created), templates/maintenance.html (created), backup/router.py, main.py, tests/backup/test_backup_ui.py, tests/backup/test_maintenance.py (created)
**Commit**: 72ac4bb feat: restore backups from the admin page under maintenance mode
**Notes**:
- 413 uses the full too-large message as alert and restore-file field error; rollback-failed 500 variant has no Retry-After; missing file part → generic 400 RESTORE_SUMMARY.
- Residual risks for Group 10/verification: early UploadError (Content-Length 413/415/malformed) and the 409 busy path render the page (rotating CSRF) before CSRF is verified; a non-RestoreFailedError exception from apply_restore would leave maintenance active.
- Group 10 gap-test candidates: rollback-failed path over HTTP, drain timeout 500, malformed multipart 400 with empty staging, 422 missing-admin / unsupported-revision over HTTP, invalid journal at startup.

### Group 8: Importable Preflight and Offline Restore CLI
**From Implementation Plan**: global/coding-style, global/error-handling, backend/configuration, security/data-protection, ai-assistant, global/minimal-implementation, testing/test-writing
**From INDEX.md**: global/conventions, backend/migrations (head via ScriptDirectory without alembic.ini)
**Discovered During Execution**: ruff TRY004 — kept ValueError per spec with `# noqa: TRY004` + reason

## 2026-10-14 - Group 8 Complete

**Steps**: 8.1 through 8.7 completed
**Tests**: 6 new (1 characterization); group 28 passed; full suite 182 passed; ruff clean; ps1 parses; protected hashes unchanged
**RED**: ImportError preflight / backup.restore at collection
**Files Modified**: src/medical_archive/preflight.py (created), __main__.py, backup/restore.py (created), tools/check_local_ai.py, scripts/restore-backup.ps1 (created), tests/test_release_guards.py (created), tests/backup/test_restore_cli.py (created), tests/test_network_config.py
**Commit**: 4fb9f27 feat: add importable preflight and offline restore cli
**Smoke**: isolated scratch data dir → status failed (no migrated DB, no tesseract); LM Studio models detected via loopback GET only.
**Notes**:
- CLI failures print to stderr + SystemExit(1); pre-copy size/existence check; dispose no-op offline.
- TESSERACT_CMD / LM_STUDIO_PORT overrides removed from the old tool.
- Group 9: README ~127–131 still references $env:LM_STUDIO_PORT — replace with MEDICAL_ARCHIVE_LM_STUDIO_BASE_URL and `python -m medical_archive.preflight`; check-release step 5 may parse `Preflight status: degraded` as a warning; runbook must say a fresh install fails preflight until `alembic upgrade head`.
- Group 10 candidates: TLS-unreadable → failed, ocr-pol missing, malformed /models payload, CLI wrong-password / unsupported-revision / rollback-failed mapping.

### Group 9: Release Checks and Polish Runbook
**From Implementation Plan**: global/conventions, security/data-protection, backend/configuration, ai-assistant (LM Studio loopback), global/minimal-implementation
**From INDEX.md**: global/error-handling (failing step named, degraded visible), backend/migrations (downgrade/upgrade cycle)
**Discovered During Execution**: security/authentication (bootstrap is first-admin-only → no invented reset command)

## 2026-10-15 - Group 9 Complete

**Steps**: 9.1 through 9.5 completed
**Verification**: RED CommandNotFoundException; check-release.ps1 exit 0 on pwsh 7 and PS 5.1 (preflight degraded: tesseract missing); fail-abort demo `Release check failed at step 'ruff check'`; 3 scripts parse 0 errors; git status clean of DB/backup/cert/key/.env; 182 passed; ruff clean; protected hashes unchanged. Isolated via scratchpad data dir; ~/.medical-archive untouched.
**Files Modified**: scripts/check-release.ps1 (created), README.md (Polish 12-section runbook)
**Commit**: 144d857 docs: add local deployment release checks
**Manual drills**:
1. Synthetic CLI restore drill — done, passed (wrong password exit 1, live DB byte-identical; correct password exit 0; counts restored (1,1,0); no leftovers). restore-backup.ps1 not run interactively (getpass reads console).
2. UI restore drill — covered by acceptance-6 HTTP roundtrip test; real-browser drill deferred to E2E phase.
3. LAN/phone HTTPS without cert warning — pending owner.
4. App port closed outside LAN — pending owner.
5. LM Studio loopback-only — local evidence: only 127.0.0.1:1234 listening; LAN-device check pending owner.
**Notes**: gate messages English; no admin-password reset exists (documented: restore older backup); duplicated alembic log lines harmless (candidate for review).

### Group 10: Test Review & Gap Analysis
**From Implementation Plan**: testing/test-writing, global/error-handling, security/data-protection, security/authentication, backend/configuration, global/coding-style
**From INDEX.md**: global/validation, frontend/accessibility
**Discovered During Execution**: ruff ISC004 (parenthesised implicit concatenation in parametrize lists)

## 2026-10-15 - Group 10 Complete

**Steps**: 10.1 through 10.5 completed
**Tests**: 10 new functions (24 cases); feature run 122 passed; full suite 206 passed; ruff clean; protected hashes unchanged
**Meaningfulness**: each new test failed against a deliberate production mutation (restored byte-for-byte afterwards)
**Files Modified**: tests/backup/test_restore_safety.py, tests/backup/test_backup_ui.py, tests/backup/test_archive_format.py, tests/test_network_config.py
**Commit**: 959beba test: cover backup recovery and network config edge cases
**Product bugs found**: none
**Skipped (cap)**: preflight TLS-unreadable / ocr-pol / malformed /models; CLI error-message mapping; parser-level http.disconnect
**Note for verification**: shared conftest `app` fixture uses Settings(testing=True) default data_dir, whose validator creates ~/.medical-archive if missing (pre-existing).

## 2026-10-15 - Implementation Complete

**Total Steps**: 65 completed (0 unchecked)
**Commits**: 6e4148e, 8fa058a, b406f5e, 9dedfd8, ae93a21, d44241e, 72ac4bb, 4fb9f27, 144d857, 959beba on feat/plan-4-backup-tls (not pushed)
**Test Suite**: 206 passed (from 111 baseline), 1 pre-existing warning; ruff check + format clean; protected hashes unchanged
**Pending owner**: LAN/phone HTTPS, outside-LAN port closed, LM Studio LAN-device check

## 2026-10-15 - Verification Fix (Phase 11)

**Issue**: L1 — a non-RestoreFailedError exception from apply_restore left maintenance active until restart (user selected this fix only; V1, V2, L3, L4 deferred by user).
**Fix**: backup/router.py — `except Exception: maintenance.exit(); raise` after the RestoreFailedError branch; RestoreRollbackFailedError still keeps maintenance active.
**Test**: test_unexpected_apply_error_exits_maintenance (RED: `assert True is False`; GREEN after fix)
**Gates**: 207 passed; ruff check + format clean; protected hashes unchanged
