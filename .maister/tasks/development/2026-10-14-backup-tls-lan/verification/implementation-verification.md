# Implementation Verification — Plan 4 (backup/restore, TLS/LAN, release checks)

**Date**: 2026-10-15
**Branch**: feat/plan-4-backup-tls (6e4148e..959beba, 10 commits, not pushed)
**Overall Status**: ⚠️ Passed with Issues

## Executive Summary
All 65 plan steps are implemented with matching code and tests, and the full suite passes (206 tests). The reality check ran export and restore end to end on synthetic data through the real app factory. A wrong password, a corrupted backup, and a simulated power loss during the swap all left the live data intact or recovered it. No critical issues were found. What remains: the owner's manual LAN drills, two medium operational gaps (offline restore while the server is running; restore on an empty data dir), and stale docs.

## Breakdown

| Check | Result | Source |
|---|---|---|
| Implementation plan | ✅ 65/65 steps (100%), one commit per group | completeness checker |
| Test suite | ✅ 206 passed (skipped in verification: passed during implementation; re-run by the reality assessor: 206 passed) | executor / reality assessor |
| Standards compliance | ⚠️ 18 of the 19 applicable standards followed fully; backend/migrations.md text is stale | completeness checker |
| Documentation | ⚠️ Work log and Polish README complete; two project docs have a broken start command | completeness checker |
| Reality check | ⚠️ Conditional GO: works end to end; manual LAN drills pending; 2 medium gaps | reality assessor (`verification/reality-check.md`) |
| Code review / pragmatic / production readiness | — skipped (user choice) | — |

## Issues Requiring Attention

### Warnings
| ID | Source | Issue | Location | Fixable |
|---|---|---|---|---|
| V1 | reality | The offline CLI restore does not check that the server is stopped. The operation lock is per process, so a restore can swap the DB under a running server. | `src/medical_archive/backup/restore.py` | yes (cross-process lock file) |
| V2 | reality | Restore onto a fresh or empty data dir fails with a misleading "rolled back" message, because there is no live DB to snapshot. README §8 does not say to run `alembic upgrade head` first. | `backup/service.py` `_snapshot_live_database`, README §8 | yes |
| V3 | completeness | The migrations standard describes the old env.py URL precedence. | `.maister/docs/standards/backend/migrations.md:30` | yes (via /maister:standards-update) |
| V4 | completeness | The project docs still use `python -m uvicorn medical_archive.main:app`, which no longer works. | `.maister/docs/project/architecture.md:91`, `tech-stack.md:26` | yes |
| V5 | both | Manual LAN/TLS drills are pending: phone HTTPS without a warning, port closed off-LAN, LM Studio unreachable from the LAN. A real §9 backup drill is also pending. | README §9, §11 | no (owner) |

### Info / Low
| ID | Issue | Location | Fixable |
|---|---|---|---|
| L1 | A non-`RestoreFailedError` exception from `apply_restore` leaves maintenance mode on until restart. | `backup/router.py` ~264–270, `backup/service.py` ~318 | yes |
| L2 | Early upload errors and the 409 busy path render the page and rotate CSRF before CSRF is verified. Admin-only, SameSite=Strict. | `backup/router.py` ~167–174, 221–226 | yes (optional) |
| L3 | The shared `app` fixture creates `~/.medical-archive` if it is missing (pre-existing). | `tests/conftest.py` | yes |
| L4 | `# noqa: TRY004` is inert: TRY rules are not enabled. | `src/medical_archive/preflight.py:54` | yes |
| L5 | The README does not mention the Windows path-length limit (long `data_dir` breaks staging) or peak RAM of about 2–3× the archive size. | README §12 | yes |
| L6 | "Planned / not implemented" status lines are stale. | standards/backend/configuration.md:11, security/data-protection.md:18, project/architecture.md | yes |
| L7 | A body with no file part returns a generic 400 with no `restore-file` field error (browsers always send the file part). | `backup/router.py` | optional |
| L8 | There is no admin-password reset; the README documents restoring an older backup instead. | — | future |
| L9 | Tesseract is not on PATH, so preflight is degraded (environment, not plan 4). | — | owner |
| L10 | The scripts call plain `python`, so the venv must be active. The private-address check accepts some reserved ranges via `is_private` (harmless). | scripts, config.py | info |

## Recommendations
1. Fix V1 and V2, the data-safety and usability gaps in offline and new-machine restore.
2. Fix L1, so maintenance mode always exits unless the rollback failed.
3. Update the docs in the documentation phase (V4, L5, L6), and update the migrations standard (V3) via /maister:standards-update.
4. Owner: run README §9 and §11 drills and record the results in the work log.

## Roadmap
Not updated in this phase.

## Verification Checklist
- [x] Completeness checker invoked
- [x] Test suite: skipped (passed during implementation); re-run by the reality assessor: 206 passed
- [x] Reality check invoked (`verification/reality-check.md`)
- [x] Code review, pragmatic review and production readiness skipped by user choice
- [x] Results compiled; overall status determined

## Fixes Applied (Phase 11)
- **L1 fixed**: commit 6b15cf1 `fix: always exit maintenance after unexpected restore errors`. New test `test_unexpected_apply_error_exits_maintenance` failed before the fix and passes after it. Full suite 207 passed.
- **Deferred by user**: V1 (cross-process lock for CLI restore; until fixed, always stop the server before `restore-backup.ps1`), V2 (restore onto an empty data dir; run `alembic upgrade head` first), L3, L4.
- **Docs phase**: V4, L5, L6. V3 via /maister:standards-update.
