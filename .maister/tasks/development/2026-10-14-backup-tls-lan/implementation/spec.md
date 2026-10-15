# Specification: Plan 4 — encrypted backup/restore, TLS and LAN-only serving, release checks

## Goal
Let the household administrator export an encrypted (AES-256-GCM + scrypt) full backup of the SQLite database and every original file, and restore it without ever overwriting live data with a bad archive. Serve the app over HTTPS on an explicit private LAN address with a fail-closed production configuration, and add a release gate, an importable preflight and a Polish recovery runbook.

Satisfies design-spec acceptance criteria **6** (an encrypted backup restores to a consistent state; a wrong password or corruption is detected without overwriting data) and **7** (no data leaves the host; the model service is unreachable from the LAN). Covers plan `docs/superpowers/plans/2026-10-13-backup-i-lan.md`, Tasks 1–4, plus `scripts/restore-backup.ps1`.

---

## Scope

### In scope
1. Versioned encrypted envelope (`backup/crypto.py`) and strict ZIP archive format (`backup/format.py`).
2. WAL-safe export `create_backup` (`backup/service.py`), with an all-profiles confirmation and a size cap.
3. Two-phase restore:
   - stage and validate fully in a private directory;
   - then, under maintenance and a process-wide lock, take a snapshot, copy, swap and health-check;
   - roll back on failure;
   - a startup journal recovers an interrupted restore.
4. Admin UI at `/settings/backup` with export and restore forms, a nav link, a standalone 503 maintenance page and a login notice after a restore.
5. A streaming multipart parser variant: a text-field allow-list plus one file streamed to disk with a cap. Document upload behaviour is unchanged.
6. Offline restore CLI `python -m medical_archive.backup.restore` and its wrapper `scripts/restore-backup.ps1`.
7. Fail-closed `Settings`:
   - environment, host, port, TLS and allowed-hosts fields, plus the backup cap;
   - LM Studio loopback check;
   - a production model validator.
8. `create_app` changes:
   - strict outside testing;
   - module-level `app` removed;
   - TrustedHost middleware in production, no CORS, docs stay disabled;
   - `python -m medical_archive` (`serve()`) with TLS;
   - `scripts/run-lan.ps1`.
9. Importable `src/medical_archive/preflight.py` returning ok/degraded/failed. `tools/check_local_ai.py` becomes a thin wrapper around it.
10. `scripts/check-release.ps1`.
11. Polish README runbook.
12. `pyproject.toml`: add `cryptography`; move `sqlite-vec` to runtime dependencies.
13. `.gitignore`: add `*.marchive`.

### Out of scope
- An offline export CLI. Export is UI-only (decision 13).
- A chunked/streamed envelope (format v2) and archives over 1 GiB.
- A `python-multipart` dependency.
- Automatic certificate generation or self-signed fallback.
- Scheduled or automatic backups, remote/cloud targets and backup listing on the server.
- The "last backup" timestamp hint (mockup 7). Only the static reminder is shown.
- A JSON variant of the 503 maintenance response.
- Rate limiting of admin password attempts.
- A secure-delete implementation (documented as a Windows limitation).
- Refactoring the duplicate form parsers in `documents/router.py` and `measurements/router.py`.
- Updating `.maister/docs` status lines. That belongs to the documentation phase.

---

## User Stories
- As the **household admin**, I want to download an encrypted backup of everything, so that I can recover after a disk failure or a bad update.
- As the **admin**, I want a wrong password or a damaged file to be rejected without touching current data, so that a failed restore cannot cost me data.
- As the **admin**, I want to restore from the host's command line when the web UI cannot be used.
- As a **household member**, I want to reach the app from my phone over trusted HTTPS on the home LAN only, so that my records are never exposed beyond the home network.
- As the **maintainer**, I want one release command that runs lint, tests, migrations and preflight, so that I know the installation is ready for real data.

## User Journey
1. **Admin, export.**
   - Clicks the nav link "Kopia zapasowa" (after "Zaproszenia", shown to admins only) and opens `/settings/backup`.
   - Reads the warning, enters the admin password and the backup password twice, ticks the confirmation, and clicks "Utwórz i pobierz kopię".
   - The browser downloads `medyczne-archiwum-YYYYMMDD-HHMMSS.marchive` and stays on the page.
2. **Admin, restore (web).**
   - In the "danger zone" chooses the file, enters the backup password and the admin password, ticks the confirmation, and clicks "Odtwórz z kopii".
   - While the swap runs, every other request gets the 503 maintenance page.
   - The admin's request ends with a 303 to `/auth/login?restored=1`, which shows the fixed notice. Everyone has to log in again, with the credentials that the backup contains.
3. **Admin, restore (offline).**
   - Stops the server and runs `.\scripts\restore-backup.ps1 -BackupFile <path>`.
   - Types the backup password and the confirmation word.
   - Starts the server again.
4. **Member.** Sees no link. `/settings/backup*` returns 403. An unauthenticated request gets a 303 to `/auth/login`.
5. **Maintainer.**
   - Sets the `MEDICAL_ARCHIVE_*` production variables and creates mkcert certificates.
   - Runs `.\scripts\check-release.ps1`, then `.\scripts\run-lan.ps1 -HostAddress 192.168.1.10 -Port 8443 -CertFile … -KeyFile …`.

---

## Core Requirements
1. The envelope layout is exactly `MARCHIVE | version=1 | salt_len=16 | nonce_len=12 | salt | nonce | ciphertext+tag`. The whole 39-byte header is the AES-GCM AAD.
2. scrypt parameters:
   - N=2**15, r=8, p=1, dklen=32, maxmem 64 MiB;
   - the password is encoded as UTF-8;
   - a fresh random salt and nonce for every encryption.
3. Envelope size and header are validated before the KDF runs. `MAX_ENVELOPE_BYTES` (1 GiB) is read at call time.
   - Header or length errors raise `InvalidBackup`.
   - A wrong password or a tag mismatch raises `InvalidBackupPassword`, a subclass of `InvalidBackup`.
4. The archive is a ZIP with `manifest.json`, `database.sqlite3` and `files/<storage_key>` entries. `validate_archive` is an allow-list:
   - no unlisted, missing or duplicate entries;
   - no path tricks;
   - bounded sizes, with `MAX_MANIFEST_BYTES` read at call time;
   - every file's sha256 and size match the manifest;
   - the manifest never contains absolute paths.
5. Export:
   - takes an online-backup-API snapshot (WAL content included);
   - reads storage keys and schema revision from the snapshot;
   - requires explicit confirmation (`BackupConfirmationRequired`) and a valid backup password (`validate_password`);
   - fails early if the estimated size exceeds the cap;
   - deletes plaintext temp files under `data_dir/.backup-tmp` in `finally`.
6. Restore phase 1 (non-destructive):
   - decrypt;
   - validate the archive and extract it to `data_dir/.restore-staging/<op>/staged`;
   - SQLite `integrity_check` and `foreign_key_check`;
   - schema revision: head is accepted; an older known revision is upgraded on the staged copy only; a newer or unknown one is rejected;
   - at least one admin account exists;
   - `rag_chunk_vectors` is queryable with sqlite-vec;
   - the storage keys in the DB equal the manifest.
7. Restore phase 2:
   - runs under the process-wide lock, in maintenance mode, with in-flight requests drained and the engine disposed;
   - takes a rollback snapshot;
   - copies the staged DB into the live DB with the backup API, swaps `documents/` by rename, wipes all sessions, and runs a health check;
   - a journal at `data_dir/.restore-journal.json` records each state;
   - any failure rolls back before traffic resumes.
8. At startup (server or CLI) an unresolved journal is rolled back (or cleaned up if it reached `committed`), and stale temp and staging directories are removed.
9. The HTTP routes are admin-only and protected by CSRF and an admin password re-entry.
   - Status codes: 422 for an invalid archive (identical text for corruption and wrong password), 413 over the cap, 409 when busy, 403 for a member, CSRF failure or wrong admin password, 400 for field errors, 500 for a rolled-back failure.
   - Success: 303 to `/auth/login?restored=1`.
   - Passwords are never logged or echoed.
10. While a restore swap runs, every path except `/health` (including `/static`) returns 503 with the standalone maintenance page.
11. Settings:
    - `environment` defaults to `development`, and development allows only a loopback host;
    - production requires the session secret, existing TLS cert and key files, and an explicit private LAN IP (rejecting unspecified, loopback, multicast, link-local, reserved and public addresses);
    - the LM Studio URL is loopback-only in every environment;
    - `allowed_hosts` rejects wildcards.
12. `create_app(testing=False)` builds strict Settings, runs the backup recovery, wires the `BackupService`, adds TrustedHost in production and does not add CORS. The module-level `app` is removed.
13. `python -m medical_archive` validates Settings and runs preflight; a `failed` result stops startup in production. It then runs uvicorn with TLS, `proxy_headers=False` and `server_header=False`.
14. Preflight is importable and its probes are injectable. Statuses:
    - `failed` for python, sqlite-vec, database, data_dir or TLS (TLS only in production);
    - `degraded` for pymupdf, OCR binary, Polish language pack, embedding model or generation model.

    It exposes `missing_components`.
15. Scripts:
    - `check-release.ps1` runs ruff check, ruff format --check, pytest, alembic upgrade/downgrade/upgrade on a temporary DB, preflight, and a check that `git status` lists no DB, backup, certificate or key files;
    - `run-lan.ps1` and `restore-backup.ps1` are thin wrappers.
16. The README is in Polish and covers mkcert and the client CA install, the firewall Private profile, LM Studio on localhost, backup before an update, restore through the UI and the CLI, a lost backup password, a lost admin password and the isolation check. The "not ready for real data" warning is replaced.
17. `tests/backup/test_crypto.py` and `tests/backup/test_roundtrip.py` pass **unmodified**.

---

## Visual Design
Source: `analysis/ui-mockups.md` (approximate fidelity: structure, classes and **exact Polish strings** are binding; colours follow the suggested values). There are no image mockups.

### Templates
| File | Change |
|---|---|
| `templates/base.html` | Inside `{% if user.is_admin %}`, after "Zaproszenia", add `<a href="/settings/backup">Kopia zapasowa</a>`, with `aria-current="page"` when the path starts with `/settings`. |
| `templates/backup.html` (new) | Extends `base.html`. Contains `section.panel[aria-labelledby=backup-heading]` with `h1#backup-heading` "Kopia zapasowa", followed by the intro text, `div.info-box`, the export `section[aria-labelledby=export-heading]` and the restore `section.danger-zone[aria-labelledby=restore-heading]`. No JavaScript, no meta refresh. |
| `templates/maintenance.html` (new) | Standalone `<html lang="pl">` page with an inline `<style>`: `#16324f` heading, `#1f2937` text on `#f3f6fa`, system-ui font. No `/static` link, no user/request context. Contains the h1, two paragraphs and an "Odśwież stronę" link (`href=""`). |
| `templates/login.html` | Add `<link rel="stylesheet" href="/static/app.css">`. Add an optional `<p role="status" class="notice-success">` rendered when `notice == "restored"`. |
| `static/app.css` | Append about 45 lines: `.warning-box`, `.warning-box-danger`, `.warning-box ul`, `.info-box`, `.checkbox-field` (+ checkbox override), `input[type="file"]`, `.field-hint`, `.field-error`, `input[aria-invalid="true"]`, `button.button-danger` (+ hover), `.danger-zone`, `[role="status"].notice-success`. Use the values from the mockup table. |

### Form fields (ids and names are binding)
| Form | id | name | Attributes |
|---|---|---|---|
| export | (hidden, first) | `csrf_token` | — |
| export | `export-admin-password` | `admin_password` | `password required autocomplete=current-password` |
| export | `export-backup-password` | `backup_password` | `password required minlength=12 maxlength=1024 autocomplete=new-password aria-describedby=export-password-hint` |
| export | `export-backup-password-repeat` | `backup_password_repeat` | same as above |
| export | `export-confirm` | `confirm` | `checkbox value=yes required` |
| restore | (hidden, **first part**) | `csrf_token` | — |
| restore | `restore-file` | `backup_file` | `file accept=".marchive" required aria-describedby=restore-file-hint` |
| restore | `restore-backup-password` | `backup_password` | `password required maxlength=1024 autocomplete=off` |
| restore | `restore-admin-password` | `admin_password` | `password required autocomplete=current-password` |
| restore | `restore-confirm` | `confirm` | `checkbox value=yes required` |

Form-level attributes:
- Export: `action="/settings/backup/export#export-error" method=post aria-describedby=export-warning`.
- Restore: `action="/settings/backup/restore#restore-error" method=post enctype=multipart/form-data aria-describedby=restore-warning`.
- Password inputs are never pre-filled and checkboxes are never re-checked after an error.

### Page strings (exact)
| Element | Text |
|---|---|
| title / h1 | `Kopia zapasowa` |
| intro | `Kopia obejmuje bazę danych i pliki wszystkich kont oraz profili. Plik jest zaszyfrowany hasłem kopii; pobierasz go na swoje urządzenie.` |
| info box | `Utwórz kopię przed każdą aktualizacją aplikacji. Przechowuj plik i hasło kopii oddzielnie. Zgubionego hasła kopii nie da się odzyskać.` |
| export h2 | `Utwórz kopię zapasową` |
| export warning (`#export-warning`, `.warning-box`) | `Uwaga: kopia zawiera dane wszystkich profili. Każdy, kto ma plik kopii i zna hasło kopii, może odczytać wszystkie dane. Przechowuj ją w bezpiecznym miejscu.` |
| labels | `Twoje hasło administratora` / `Hasło kopii` / `Powtórz hasło kopii` / `Rozumiem, że kopia zawiera dane wszystkich profili` |
| hint `#export-password-hint` | `Od 12 do 1024 znaków. Bez tego hasła kopii nie da się odtworzyć.` |
| export submit | `Utwórz i pobierz kopię` |
| restore h2 | `Odtwórz z kopii zapasowej` |
| restore warning (`#restore-warning`, `.warning-box.warning-box-danger`) | lead: `Odtworzenie zastąpi obecne dane wszystkich kont i profili danymi z kopii. Tej operacji nie można cofnąć.`; list items: `Wszyscy użytkownicy, także Ty, zostaną wylogowani.` / `Konta i hasła logowania będą takie, jak w kopii — zaloguj się hasłem, które obowiązywało w chwili tworzenia kopii.` / `W czasie odtwarzania aplikacja jest niedostępna dla wszystkich.` |
| labels | `Plik kopii (.marchive)` / `Hasło kopii` / `Twoje hasło administratora` / `Rozumiem, że obecne dane zostaną zastąpione` |
| file hint `#restore-file-hint` | `Maksymalnie {max_backup_size_label}.` (for example `Maksymalnie 1 GiB.`) |
| restore submit (`button.button-danger`) | `Odtwórz z kopii` |
| under restore button (`.field-hint`) | `Odtwarzanie może potrwać kilka minut. Nie zamykaj tej karty.` |
| maintenance title / h1 | `Trwa odtwarzanie kopii zapasowej…` |
| maintenance body | `Aplikacja jest chwilowo niedostępna. Spróbuj ponownie za kilka minut.` / `Po zakończeniu wszyscy użytkownicy muszą zalogować się ponownie.` / link `Odśwież stronę` |
| login notice | `Kopia została odtworzona. Zaloguj się ponownie.` + `Użyj hasła, które obowiązywało w chwili tworzenia kopii.` |

### Error presentation
- Each response has exactly one `p[role=alert]` summary, at the top of the submitted form (`#export-error` or `#restore-error`).
- Each faulty field gets `aria-invalid="true"` and `aria-describedby` pointing at its `p.field-error` (`✖ <text>`).
- The other form renders without errors.
- Messages never contain the filename, paths, exception text or record data.

### Error catalogue (exact strings and status codes)
`EXPORT_SUMMARY` = `Nie utworzono kopii. Popraw zaznaczone pola.`
`RESTORE_SUMMARY` = `Nie odtworzono kopii. Obecne dane nie zostały zmienione.`

| Situation | Status | Form | Summary | Field + `field-error` |
|---|---|---|---|---|
| Wrong admin password | 403 | export | EXPORT_SUMMARY | admin password: `Hasło administratora jest nieprawidłowe.` |
| Wrong admin password | 403 | restore | RESTORE_SUMMARY | admin password: same text |
| Backup password outside 12–1024 | 400 | export | EXPORT_SUMMARY | `export-backup-password`: `Hasło kopii musi mieć od 12 do 1024 znaków.` |
| Repeat mismatch | 400 | export | EXPORT_SUMMARY | `export-backup-password-repeat`: `Hasła kopii nie są takie same.` |
| Export confirmation missing | 400 | export | EXPORT_SUMMARY | `export-confirm`: `Zaznacz potwierdzenie, aby utworzyć kopię.` |
| Restore confirmation missing | 400 | restore | RESTORE_SUMMARY | `restore-confirm`: `Zaznacz potwierdzenie, aby odtworzyć kopię.` |
| No file / empty file | 400 | restore | RESTORE_SUMMARY | `restore-file`: `Wybierz plik kopii (.marchive).` |
| Malformed multipart body (not CSRF) | 400 | restore | RESTORE_SUMMARY | none |
| Corrupt file, wrong backup password, bad tag, traversal, manifest mismatch, unsupported format | 422 | restore | `Nie można odtworzyć kopii: plik jest uszkodzony albo hasło jest nieprawidłowe. Obecne dane nie zostały zmienione.` | none |
| Upload larger than cap | 413 | restore | `Plik kopii jest za duży (maksymalnie {label}). Obecne dane nie zostały zmienione.` | `restore-file` marked |
| Export exceeds cap | 413 | export | `Nie można utworzyć kopii: dane przekraczają limit {label}.` | none |
| No admin in backup | 422 | restore | `Nie można odtworzyć kopii: nie zawiera aktywnego konta administratora. Obecne dane nie zostały zmienione.` | none |
| Newer or unknown schema revision | 422 | restore | `Nie można odtworzyć kopii: została utworzona w nowszej lub nieznanej wersji aplikacji. Zaktualizuj aplikację i spróbuj ponownie. Obecne dane nie zostały zmienione.` | none |
| Lock held | 409 | either | `Operacja kopii zapasowej już trwa.` | none |
| Phase-1 I/O error, sqlite-vec missing, drain timeout, or swap failed and rolled back | 500 | restore | `Odtwarzanie nie powiodło się i zostało wycofane. Obecne dane nie zostały zmienione.` | none |
| Export source error (missing original, invalid storage key, unreadable DB) — **new** | 500 | export | `Nie można utworzyć kopii. Szczegóły zapisano w dzienniku aplikacji.` | none |
| Rollback itself failed — **new** | 500 | standalone `maintenance.html` variant (app stays in maintenance) | h1 `Odtwarzanie nie powiodło się` + `Przywracanie poprzednich danych zostanie dokończone po ponownym uruchomieniu aplikacji. Uruchom aplikację ponownie i nie usuwaj katalogu danych.` | — |
| CSRF failure | 403 | either | existing plain `Invalid or missing CSRF token.` (no page) | — |
| Member | 403 | any route | existing `Administrator access is required.` | — |

The last two "new" rows are not in the mockup catalogue. They are needed because these failure states exist and the mockup has no text for them. Both follow the catalogue's tone and rules.

---

## Reusable Components

### Existing code to leverage
| Element | Path | Use |
|---|---|---|
| `require_admin`, `get_authenticated_session`, `get_current_user` | `src/medical_archive/auth/dependencies.py` | Route guards (member 403, unauthenticated 303). |
| `verify_csrf`, `rotate_csrf`, `validate_password` | `src/medical_archive/auth/service.py` (L104, L110, L48) | CSRF check and rotation on every page render. Backup password policy (12–1024). |
| `verify_password` | `src/medical_archive/auth/passwords.py` | Admin password re-entry against the current DB. |
| Invitation GET/POST pattern | `src/medical_archive/auth/router.py` L189–231 | Admin page: rotate CSRF, commit, render. |
| `_form_fields` | `src/medical_archive/auth/router.py` L52–70 | Moved to `web/forms.py` as `read_form_fields` and reused by the export POST. The auth router imports it. |
| `render_template`, `templates` | `src/medical_archive/web/templates.py` | All page renders. The middleware renders `maintenance.html` through the same `templates` environment. |
| `read_multipart_file` state machine, `UploadError` | `src/medical_archive/documents/storage.py` | Extracted into a shared private part iterator. The new `read_multipart_form` builds on it. |
| `write_document` atomic pattern (temp + fsync + `os.replace`) | `src/medical_archive/documents/storage.py` | Same pattern for the encrypted destination file and the restore journal. |
| `load_sqlite_vec`, `VectorExtensionUnavailableError` | `src/medical_archive/rag/index.py` | vec0 validation on the staged DB and the preflight sqlite-vec probe. |
| `create_database` | `src/medical_archive/db.py` | Engine creation, unchanged. |
| Bootstrap CLI (argparse + getpass + strict `Settings`) | `src/medical_archive/auth/bootstrap.py` | Template for `backup/restore.py`. |
| LM Studio loopback rule | `src/medical_archive/rag/embeddings.py:55`, `rag/service.py:77` | Same rule (http/https, host in {127.0.0.1, localhost, ::1}, no userinfo, query or fragment) applied as a Settings field validator. The client checks stay as defence in depth. |
| Probe logic (Python version, sqlite-vec, PyMuPDF, Tesseract + `pol`, LM Studio `/v1/models` without redirects) | `tools/check_local_ai.py` | Moved into `preflight.py`. The tool becomes a wrapper. |
| `app.state` collaborator injection | `documents/router.py:144`, `rag/router.py:38` | `app.state.backup_service` and `app.state.maintenance`. |
| alembic `ScriptDirectory`, `command.upgrade` | `migrations/env.py`, `tests/conftest.py` | Revision classification and staged-copy upgrade. |
| Test fixtures | `tests/conftest.py` (`auth_engine`, `auth_session_factory`, `auth_db`, `app`, `client`, `admin_user`, `member_user`, `extract_csrf_token`) | All HTTP and restore tests. `auth_engine.dispose` is the injected dispose hook. |
| `_login` helper pattern | `tests/auth/test_authentication.py:15` | Copied as a private helper in the backup test conftest (project convention: per-package helpers). |

### New components required
| Component | Why existing code cannot be reused |
|---|---|
| `backup/crypto.py`, `backup/format.py` | No envelope or archive code exists. The contract is fixed by the existing tests. |
| `backup/service.py` (export, staging, apply, rollback, journal, recovery, lock) | No backup or restore logic exists. The journal is needed because the DB and `documents/` cannot be swapped atomically together. |
| `backup/maintenance.py` (state + ASGI middleware) | No middleware exists. Draining in-flight requests is needed because Windows refuses to rename a directory with open handles, and an open read transaction blocks the backup-API write. |
| `backup/router.py`, `templates/backup.html`, `templates/maintenance.html` | New UI. |
| `backup/restore.py` (CLI) | Recovery path for when the UI cannot be used (decision 4). |
| `read_multipart_form` in `documents/storage.py` | The existing parser accepts only one in-memory `file` field (25 MiB). Restore needs text fields plus a 1 GiB file streamed to disk. The state machine is shared, not duplicated. |
| `web/forms.py::read_form_fields` | A move of the private auth helper, so the backup router does not import another domain's router internals. |
| `db.reset_database_engine()` | The engine is hidden inside an `lru_cache`. Restore must dispose it and clear the cache. |
| `src/medical_archive/__main__.py` (`serve`) | Replaces the import-time `app = create_app()`, which cannot be strict (decision 3). |
| `src/medical_archive/preflight.py` | `tools/check_local_ai.py` cannot be imported or injected (decision 12). |
| `scripts/*.ps1` | No `scripts/` directory exists. |

---

## Technical Approach

### Module map (files to create or modify)

| File | Status | Responsibility and public surface |
|---|---|---|
| `src/medical_archive/backup/__init__.py` | new | Empty package marker. |
| `src/medical_archive/backup/crypto.py` | new | See "Envelope format". Public: `MAGIC`, `FORMAT_VERSION`, `SALT_BYTES`, `NONCE_BYTES`, `HEADER_BYTES` (39), `SCRYPT_N`, `SCRYPT_R`, `SCRYPT_P`, `KEY_BYTES`, `SCRYPT_MAXMEM`, `MAX_ENVELOPE_BYTES`. `class InvalidBackup(Exception)` ("Raised when a backup envelope or archive is malformed, unsupported or unsafe."). `class InvalidBackupPassword(InvalidBackup)`. `encrypt(payload: bytes, password: str) -> bytes`. `decrypt(envelope: bytes, password: str) -> bytes`. |
| `src/medical_archive/backup/format.py` | new | See "Archive format". Public: `MANIFEST_NAME = "manifest.json"`, `DATABASE_NAME = "database.sqlite3"`, `FILES_PREFIX = "files/"`, `ARCHIVE_FORMAT_VERSION = 1`, `MAX_MANIFEST_BYTES = 1 MiB`. `write_archive(target: Path, *, database_path: Path, documents_dir: Path, storage_keys: Sequence[str], schema_revision: str) -> dict` writes the ZIP and returns the manifest. `validate_archive(payload: bytes) -> dict` returns the manifest. `extract_archive(payload: bytes, destination: Path) -> dict` validates, writes `destination/database.sqlite3` and `destination/documents/<key>`, and returns the manifest. |
| `src/medical_archive/backup/service.py` | new | Export, restore phases, journal, recovery, lock. Errors: `BackupConfirmationRequired(Exception)`, `BackupTooLargeError`, `BackupBusyError`, `BackupSourceError`, `UnsupportedBackupRevisionError(InvalidBackup)`, `MissingAdministratorError(InvalidBackup)`, `RestoreFailedError`, `RestoreRollbackFailedError(RestoreFailedError)`. Module function `create_backup(destination: Path, password: str, *, data_dir: Path, database_url: str, confirmed_all_profiles: bool = False, max_bytes: int \| None = None) -> str` returns `archive_id`. Context manager `backup_operation_lock()` raises `BackupBusyError`. `recover_backup_state(*, data_dir: Path, database_url: str) -> None`. Frozen dataclass `StagedRestore(operation_dir: Path, database_path: Path, documents_dir: Path, archive_id: str, schema_revision: str)`. `class BackupService` with keyword-only `__init__(*, data_dir, database_url, max_bytes, dispose_engine: Callable[[], None])` and methods `create_backup(destination, password, *, confirmed_all_profiles) -> str`, `new_operation_dir() -> Path`, `stage_restore(envelope_path: Path, password: str) -> StagedRestore`, `apply_restore(staged: StagedRestore) -> None`, `discard(operation_dir: Path) -> None`, and property `max_bytes`. |
| `src/medical_archive/backup/maintenance.py` | new | `class MaintenanceState` with `active: bool`, `in_flight: int`, `enter()`, `exit()`, and `async wait_until_drained(*, timeout_seconds: float) -> bool`, which returns once only the calling restore request is still in flight. `class MaintenanceMiddleware` (pure ASGI, `__init__(app, *, state)`) returns 503 with `maintenance.html` and headers `Retry-After: 30` and `Cache-Control: no-store` for every HTTP path except `/health` while active. It counts in-flight requests otherwise. |
| `src/medical_archive/backup/router.py` | new | `router = APIRouter(prefix="/settings/backup", tags=["backup"])`. GET `""`, POST `/export`, POST `/restore`. Collaborators come from `request.app.state.backup_service` and `request.app.state.maintenance`. If `backup_service` is missing (testing app without injection), the route raises `RuntimeError`, so tests can never hit a real data dir. |
| `src/medical_archive/backup/restore.py` | new | Offline CLI `main(arguments: list[str] \| None = None) -> None` plus `if __name__ == "__main__"`. |
| `src/medical_archive/templates/backup.html`, `maintenance.html` | new | See Visual Design. |
| `src/medical_archive/__main__.py` | new | `serve() -> None`: strict `Settings()`, then preflight (production `failed` raises `SystemExit(1)` and prints the missing components; development prints warnings), then `create_app(settings=settings)`, then `uvicorn.run(app, host, port, ssl_certfile, ssl_keyfile, proxy_headers=False, server_header=False)`. |
| `src/medical_archive/preflight.py` | new | See "Preflight contract". |
| `src/medical_archive/web/forms.py` | new | `async read_form_fields(request, *, max_bytes: int = 16_384, max_fields: int = 16) -> dict[str, str]`, moved unchanged from `auth/router._form_fields` (415, 413, 400 duplicate). |
| `src/medical_archive/config.py` | modify | New fields and validators. See "Configuration and production validation". |
| `src/medical_archive/main.py` | modify | `create_app(*, testing: bool = False, settings: Settings \| None = None)`. Remove module-level `app`. Details below. |
| `src/medical_archive/db.py` | modify | Split into an `lru_cache` `_database_engine()` (strict `Settings()`) and `_session_factory()` bound to it. Add `reset_database_engine() -> None`, which disposes the cached engine if present and clears both caches. |
| `src/medical_archive/documents/storage.py` | modify | Extract the private part iterator. Re-implement `read_multipart_file` on it with identical messages and limits. Add `read_multipart_form` and a frozen dataclass `MultipartForm(fields: dict[str, str], file_size: int)`. |
| `src/medical_archive/auth/router.py` | modify | Import `read_form_fields` from `web/forms.py` in place of the local helper. `login_page` gains an optional query `restored: str \| None = None` and passes `notice="restored"` only when it equals `"1"`. The value is never reflected. |
| `src/medical_archive/templates/base.html`, `login.html`, `static/app.css` | modify | See Visual Design. |
| `migrations/env.py` | modify | URL precedence becomes: programmatic `sqlalchemy.url` first, then `MEDICAL_ARCHIVE_DATABASE_URL`, then Settings. `alembic.ini` keeps the URL empty, so the CLI behaviour is unchanged. This stops an environment variable from redirecting a staged-copy upgrade to the live DB. |
| `tools/check_local_ai.py` | modify | Thin wrapper: `raise SystemExit(preflight.main(["--strict"]))`. Keeps the exit-1-on-any-missing behaviour. |
| `pyproject.toml` | modify | Add `cryptography` to runtime dependencies. Move `sqlite-vec` from `local-ai` to runtime dependencies (`local-ai` keeps `pymupdf`). |
| `.gitignore` | modify | Add `*.marchive`. |
| `README.md` | modify | Runbook. See the README outline. |
| `scripts/run-lan.ps1`, `scripts/restore-backup.ps1`, `scripts/check-release.ps1` | new | See "CLI and scripts". |

Not modified, on purpose:
- `documents/service.py` and `migrations/env.py` keep their `Settings(testing=True)` fallbacks. Production startup has already validated strict Settings, the LM Studio rule now lives in Settings, and these call sites only read model, URL and DB defaults.
- `rag/embeddings.py` and `rag/service.py` keep their client-side loopback checks.

### Envelope format (`crypto.py`)
| Offset | Length | Field |
|---|---|---|
| 0 | 8 | `MARCHIVE` (ASCII) |
| 8 | 1 | version = `0x01` |
| 9 | 1 | salt length = `16` |
| 10 | 1 | nonce length = `12` |
| 11 | 16 | salt (random, `os.urandom`) |
| 27 | 12 | nonce (random) |
| 39 | n+16 | AES-256-GCM ciphertext followed by the 16-byte tag. AAD = bytes 0–38. |

`decrypt` checks, in order:
1. `len(envelope) > MAX_ENVELOPE_BYTES` (module global looked up at call time) raises `InvalidBackup`.
2. `len < HEADER_BYTES + 16` raises `InvalidBackup`.
3. Magic, version, salt_len and nonce_len must match exactly, or it raises `InvalidBackup`.
4. Only then is the key derived, with `hashlib.scrypt(password.encode("utf-8"), salt=…, n=2**15, r=8, p=1, maxmem=64 MiB, dklen=32)`.
5. `AESGCM.decrypt`. `InvalidTag` raises `InvalidBackupPassword`, chained `from None` so no internals leak.

No plaintext is returned on any failure. `encrypt` raises `ValueError` if the result would exceed `MAX_ENVELOPE_BYTES`; the service checks earlier. The KDF parameters are not injectable: tests use the real parameters (about 32 MiB and about 0.1 s per operation).

### Archive format (`format.py`)
- **ZIP layout**:
  - `manifest.json` (DEFLATED);
  - `database.sqlite3` (DEFLATED), a self-contained snapshot with `journal_mode=DELETE`;
  - `files/<storage_key>` (STORED, because originals are already compressed PDF/JPEG/PNG).
  - No directory entries.
- **Manifest** (JSON object, exact key set):
  - `format_version`: int, must be 1;
  - `archive_id`: canonical UUID4 string;
  - `schema_revision`: string matching `^[0-9A-Za-z_]{1,64}$`;
  - `database`: object with `size` (int ≥ 0) and `sha256` (64 lowercase hex);
  - `storage_keys`: sorted, unique list of canonical lowercase UUID strings;
  - `files`: object mapping each storage key to `{size, sha256}`. Its keys must equal `storage_keys`.
  - No paths, no timestamps, no user data.
- **`validate_archive(payload)`** checks, in order. Any violation raises `InvalidBackup`; `zipfile.BadZipFile`, `json` and `UnicodeDecodeError` errors are wrapped.
  1. Open from `BytesIO`. The entry count must be ≤ 2 + 100 000.
  2. Every entry name must be exactly `manifest.json`, `database.sqlite3` or `files/` + a canonical UUID. That allow-list alone rejects absolute paths, `..`, backslashes, drive letters, `:` and ADS names, reserved names and nested paths.
  3. No duplicate names; no directory entries; no symlink mode bits in `external_attr`; no encryption flag; compression must be STORED or DEFLATED.
  4. `manifest.json` must exist and its declared `file_size` must be ≤ `MAX_MANIFEST_BYTES` (looked up at call time). Read it with a bounded read and parse it strictly.
  5. The set of entry names must equal {manifest, database} ∪ {`files/<k>` for k in `storage_keys`}. No unlisted or missing entries are allowed.
  6. For each data entry, the declared `file_size` must equal the manifest size and the sum must be ≤ `MAX_ENVELOPE_BYTES`. Stream-read each entry with a cap of size + 1 bytes (against zip-bomb lies) and compare the sha256.
- **`extract_archive`** runs the same validation, then writes the entries with exclusive-create mode into `destination`. Files go to `destination/documents/<key>`.

### Export flow (`create_backup`)
1. If `confirmed_all_profiles` is not set, raise `BackupConfirmationRequired` before any I/O.
2. Call `validate_password(password)`; a `ValueError` propagates to the caller.
3. Resolve the live DB path from `database_url` (SQLite only).
4. Work in `data_dir/.backup-tmp/<uuid4>/`.
5. Snapshot: run `sqlite3.connect(live).backup(sqlite3.connect(work/snapshot.sqlite3))`, then set `PRAGMA journal_mode=DELETE` on the snapshot. Both connections are closed in `finally`.
6. From the snapshot read:
   - `SELECT version_num FROM alembic_version` (exactly one row, otherwise `BackupSourceError`);
   - `SELECT storage_key FROM documents ORDER BY storage_key`. Every key must be a canonical UUID and `data_dir/documents/<key>` must be a regular file; otherwise `BackupSourceError`.
   - `.upload-*` temp files and unreferenced orphans are never included.
7. Estimate the size: snapshot size + sum of file sizes + 4 KiB per entry + 64 KiB. If that exceeds `max_bytes` (default: `MAX_ENVELOPE_BYTES` at call time), raise `BackupTooLargeError` before building anything.
8. Call `write_archive(work/archive.zip, …)`, streaming each file in 64 KiB chunks and computing sha256.
9. Encrypt. If `len(envelope) > max_bytes`, raise `BackupTooLargeError`.
10. Write `destination` atomically (temp in the same directory, fsync, `os.replace`).
11. Log `backup_export_succeeded archive_id=%s duration_ms=%d`.
12. In `finally`, remove the work directory. On failure, log `backup_export_failed code=%s`.

Peak memory is about twice the archive size, which the one-shot v1 format accepts.

### Restore phase 1: `stage_restore(envelope_path, password)` (never touches live data)
1. The envelope size on disk must be ≤ `max_bytes`, or it raises `InvalidBackup`.
2. Read the bytes, delete the upload file, and `decrypt`.
3. `extract_archive(payload, operation_dir/staged)`, then drop the payload reference.
4. Open the staged DB with raw `sqlite3`. A `sqlite3.DatabaseError` raises `InvalidBackup`.
   - `PRAGMA integrity_check` must return `ok`.
   - `PRAGMA foreign_key_check` must return no rows.
5. Classify the revision against alembic's `ScriptDirectory` (the chain is linear):
   - **head**: OK;
   - **known non-head (older)**: run `alembic command.upgrade(config, "head")` with `sqlalchemy.url` set to the staged path (env.py precedence fix), then re-run step 4. An upgrade failure raises `InvalidBackup`;
   - **missing, newer or unknown**: `UnsupportedBackupRevisionError`.
6. `SELECT COUNT(*) FROM users WHERE is_admin = 1` must be ≥ 1, or it raises `MissingAdministratorError`.
7. Load sqlite-vec on the staged connection and run `SELECT COUNT(*) FROM rag_chunk_vectors`.
   - `VectorExtensionUnavailableError` raises `RestoreFailedError` (server misconfiguration).
   - Any other `sqlite3.Error` raises `InvalidBackup`.
8. The set of `documents.storage_key` values must equal the manifest `storage_keys`, or it raises `InvalidBackup`.
9. Return `StagedRestore`. `OSError` anywhere raises `RestoreFailedError`. Callers `discard()` the operation directory on any error.

### Restore phase 2: `apply_restore(staged)`, state machine and rollback
Preconditions: `backup_operation_lock` is held. Then either the web path has entered maintenance, drained the requests and committed its own DB session, or the CLI is running with the server stopped.

Journal: `data_dir/.restore-journal.json` is written atomically (temp, fsync, `os.replace`). Its content:

| Key | Value |
|---|---|
| `journal_version` | `1` |
| `operation_id` | the operation UUID |
| `state` | the current state |
| `live_documents_existed` | bool |

Paths are implied by `operation_id` (`data_dir/.restore-staging/<operation_id>/{staged,rollback}`). The journal never stores absolute paths.

| # | Step | Journal state after the step |
|---|---|---|
| 1 | Call `dispose_engine()` (production: `reset_database_engine`; tests: `auth_engine.dispose`). | — |
| 2 | Snapshot the live DB with the backup API into `rollback/database.sqlite3.partial`, then rename it to `rollback/database.sqlite3`. | `prepared` (written only once the snapshot is complete) |
| 3 | Copy staged → live with the backup API (`staged_conn.backup(live_conn)`, all pages in one step, at the same live path). | `database_replaced` |
| 4 | Rename `data_dir/documents` to `rollback/documents` (if it exists), then rename `staged/documents` to `data_dir/documents`. | `documents_replaced` |
| 5 | Run `DELETE FROM sessions` on the live DB (wipes every session, including the admin's). | — |
| 6 | Health check on a fresh live connection: `PRAGMA quick_check` is `ok`; the revision is head; admin count ≥ 1; the session count is 0; every `storage_key` has a file in `data_dir/documents`. | `committed` |
| 7 | Remove `operation_dir` (staged and rollback), delete the journal, and log `backup_restore_applied archive_id=%s duration_ms=%d`. | (none) |

**Rollback.** Any `Exception` in steps 3–6 triggers `_rollback(operation_id)`:
1. If `rollback/documents` exists, or the journal says `live_documents_existed` and `data_dir/documents` is not the original: move the current `data_dir/documents` aside into the operation directory and rename `rollback/documents` back to `data_dir/documents`.
2. Copy `rollback/database.sqlite3` → live with the backup API.
3. Delete the journal and the operation directory.
4. Log `backup_restore_rolled_back code=%s`.
5. Raise `RestoreFailedError`.

If the rollback itself fails:
- the journal and the rollback data are **kept**;
- it logs `backup_restore_rollback_failed`;
- it raises `RestoreRollbackFailedError`;
- the web path then leaves maintenance **active** until restart.

The rollback snapshot is never deleted in a generic `finally`. It is deleted only after `committed` or after a successful rollback.

**Startup recovery: `recover_backup_state(data_dir, database_url)`.** It is called from `create_app(testing=False)` and from the restore CLI before any work.
- Journal state `prepared`, `database_replaced` or `documents_replaced`: run the same rollback, which always rolls back and never forward, because the health check never passed.
- State `committed`: remove the operation directory and the journal.
- Then remove every remaining entry under `.backup-tmp/` and `.restore-staging/` that is not referenced by an unresolved journal.
- An unreadable or invalid journal raises `RestoreFailedError`, so startup stops and points the user to the README.

### Web restore orchestration (router)
1. `require_admin` and `get_authenticated_session` run first (403 for a member, 303 when unauthenticated).
2. Enter `backup_operation_lock()`. If it is busy: 409 page.
3. `operation_dir = service.new_operation_dir()`.
4. `read_multipart_form` with:
   - `text_fields={csrf_token, backup_password, admin_password, confirm}`, `file_field="backup_file"`;
   - `destination=operation_dir/upload.marchive`, `max_file_bytes=service.max_bytes`;
   - a `before_file` callback that verifies CSRF against the login session and raises 403 if a token is missing or wrong, **before** any file byte is written.
5. Validate the form:
   - `confirm == "yes"`, otherwise 400 with a field error;
   - `file_size > 0`, otherwise 400 with a field error;
   - `verify_password(admin.password_hash, admin_password)`, otherwise 403 with a field error.
6. `await run_in_threadpool(service.stage_restore, …)`. Errors map to 422, 422-specific or 500 per the catalogue.
7. `database.commit()`, which releases the request's pooled connection. Then `maintenance.enter()` and `await maintenance.wait_until_drained(timeout_seconds=30)`. On timeout: `maintenance.exit()`, discard, 500.
8. `await run_in_threadpool(service.apply_restore, staged)`.
   - `RestoreFailedError`: `maintenance.exit()`, then the page is re-rendered with a 500. Re-rendering rotates CSRF and commits, which works because the snapshot restored the admin's session.
   - `RestoreRollbackFailedError`: maintenance stays on and the standalone 500 page is shown.
9. On success: `maintenance.exit()`, then `RedirectResponse("/auth/login?restored=1", 303)` with the session cookie deleted (same attributes as logout).
10. In `finally`: `service.discard(operation_dir)` unless the journal keeps it, then release the lock.

Every error re-render goes through one private helper. It calls `rotate_csrf` and `database.commit()`, then renders `backup.html` with the context `title`, `csrf_token`, `export_alert`, `export_errors`, `restore_alert`, `restore_errors`, `max_backup_size_label`. The size label is `"1 GiB"` when the size divides into whole GiB, otherwise whole MiB.

### Export orchestration (router)
1. `read_form_fields`, then CSRF (403 plain).
2. Wrong admin password: 403 page.
3. Collect the field errors in one pass: confirmation, repeat mismatch (`hmac.compare_digest`) and policy (the `ValueError` from `validate_password` maps to the length message). Any errors: 400.
4. `backup_operation_lock()`, otherwise 409.
5. `await run_in_threadpool(service.create_backup, data_dir/.backup-tmp/<uuid>.marchive, …)`. `BackupTooLargeError` gives 413; `BackupSourceError` gives 500.
6. Release the lock.
7. Return a `FileResponse` with:
   - `media_type="application/octet-stream"`;
   - `filename=medyczne-archiwum-<UTC %Y%m%d-%H%M%S>.marchive` (`Content-Disposition: attachment`);
   - `Cache-Control: no-store`;
   - `BackgroundTask` that unlinks the file after it is sent.

An error never produces a partial download; errors are always `text/html` pages.

### Multipart parser contract (`read_multipart_form`)
Keyword-only parameters:
- `text_fields: frozenset[str]`
- `file_field: str`
- `destination: Path`
- `max_file_bytes: int`
- `max_field_bytes: int = 4096`
- `before_file: Callable[[Mapping[str, str]], None]`

Rules:
- The content type must be `multipart/form-data` with a valid boundary (same check as today), otherwise 415.
- If `Content-Length` is present and exceeds `max_file_bytes + 64 KiB`, it returns 413 before reading the body.
- Each part needs `Content-Disposition: form-data` with a `name`.
- A text part has a name in the allow-list and no filename. It must be ≤ `max_field_bytes` and valid UTF-8. Duplicates, unknown names, more than `len(text_fields) + 1` parts, header blocks over 16 KiB, or data after the closing boundary all give 400.
- Exactly one file part named `file_field` is allowed. An empty filename is allowed and yields size 0. `before_file(fields_so_far)` is called when its headers are parsed. The content is streamed to `destination`, opened with `"xb"`. Exceeding `max_file_bytes` gives 413. A missing file part gives 400.
- On any error the partial destination file is deleted. Client disconnect gives 400.
- The filename and the declared MIME type are ignored and never logged.

Exceptions are `UploadError(status, detail)` with English details. The router maps them to the Polish catalogue.

`read_multipart_file` keeps its exact behaviour and messages, and is gated by the existing `tests/documents/test_upload.py`.

### Configuration and production validation (`config.py`)
New fields (env prefix `MEDICAL_ARCHIVE_`):

| Field | Type | Default | Rule |
|---|---|---|---|
| `environment` | `Literal["development", "production"]` | `"development"` | — |
| `host` | `str` | `"127.0.0.1"` | Must parse with `ipaddress.ip_address`; hostnames are rejected for the bind. |
| `port` | `int` | `8000` | `Field(ge=1, le=65535)` |
| `tls_cert` | `Path \| None` | `None` | Expanded and resolved. |
| `tls_key` | `Path \| None` | `None` | Expanded and resolved. |
| `allowed_hosts` | `list[str]` | `[]` | JSON list in env. Each entry is lowercased and must be a DNS hostname (labels `[a-z0-9-]`, 1–63 chars, total ≤ 253) or an IP literal. `*` or any wildcard is rejected. |
| `backup_max_bytes` | `int` | `1 GiB` | `Field(ge=1 MiB, le=1 GiB)`. 1 GiB is the hard ceiling of the v1 envelope. |

Validators:
- **`lm_studio_base_url`** field validator (all environments): scheme must be `http` or `https`; host in {`127.0.0.1`, `localhost`, `::1`}; no username, password, query or fragment. Otherwise: "lm_studio_base_url must use a loopback host (127.0.0.1, localhost or ::1)."
- **TLS pairing** (all environments): `tls_cert` and `tls_key` are either both set or both unset, and when set both must be existing files.
- **`model_validator(mode="after")` network profile**:
  - **development**: `host` must be loopback. Otherwise: "host must be a loopback address unless environment is production."
  - **production**: collect every problem into one `ValueError("Invalid production configuration: " + "; ".join(problems))`, so the `ValidationError` names each field:
    - `session_secret is required` when `None` (the existing field validator enforces ≥ 32 bytes);
    - `tls_cert must point to an existing certificate file`;
    - `tls_key must point to an existing private key file`;
    - `host must be an explicit private LAN address` when the address is unspecified (`0.0.0.0`, `::`), loopback, multicast, link-local (`169.254/16`, `fe80::/10`), reserved, or not `is_private`. This also rejects public and CGNAT addresses.
- The existing `__init__(testing)` secret check is unchanged. In production a missing secret is already reported by the model validator as a `ValidationError`.

### `create_app` and serving (`main.py`, `__main__.py`)
- `settings = settings or Settings(testing=testing)`. When `testing=False` this is strict and fails closed.
- Set `app.state.data_dir = settings.data_dir` and `app.state.maintenance = MaintenanceState()`.
- If not testing:
  - `recover_backup_state(...)`;
  - `app.state.backup_service = BackupService(data_dir=…, database_url=…, max_bytes=settings.backup_max_bytes, dispose_engine=reset_database_engine)`.
- Middleware:
  - `MaintenanceMiddleware(state=app.state.maintenance)` always;
  - when `environment == "production"`, also `TrustedHostMiddleware(allowed_hosts=[settings.host, *settings.allowed_hosts])`, added last so it is outermost.
- Include `backup_router`. No CORS middleware. Docs, redoc and openapi stay `None`. `/health` is unchanged.
- The module-level `app = create_app()` is **removed**. The README start command becomes `python -m medical_archive` (development, loopback http) or `.\scripts\run-lan.ps1` (production).

### Preflight contract (`preflight.py`)
- Frozen dataclasses:
  - `PreflightCheck(component: str, ok: bool, critical: bool, detail: str)`;
  - `PreflightResult(status: Literal["ok", "degraded", "failed"], checks: tuple[PreflightCheck, ...], missing_components: tuple[str, ...])`.
- `class Preflight.__init__(*, settings: Settings, fetch_model_ids: Callable[[], list[str]] | None = None)`.
  - The default `fetch_model_ids` and the other probes (`shutil.which`, `tesseract --list-langs`, PyMuPDF import, sqlite-vec load) reuse the code from `tools/check_local_ai.py`. Only the LM Studio probe is injectable, because it is the only one the tests replace. Other probes are simulated with `monkeypatch` when needed.
  - The LM Studio GET `{base}/models` uses a no-redirect opener and `embedding_timeout_seconds`, and sends **no** document data.
  - `fetch_model_ids` raising `OSError` or `ValueError` means LM Studio is unavailable.
- `check() -> PreflightResult` evaluates these components:

| Component | Critical | Check |
|---|---|---|
| `python` | yes | Version is 3.12. |
| `sqlite-vec` | yes | Loads in an in-memory connection via `load_sqlite_vec`. |
| `database` | yes | The DB file exists and its alembic revision equals head. |
| `data_dir` | yes | Writable. |
| `tls` | yes, production only (skipped in development) | Cert and key files exist and are readable. |
| `pymupdf` | no | — |
| `ocr` | no | `tesseract` is on PATH. |
| `ocr-pol` | no | `pol` is in `tesseract --list-langs`. |
| `embedding` | no | The configured `embedding_model` id is among the model ids. |
| `generation` | no | The configured `chat_model` id is among the model ids. |

  - Status: `failed` if any critical check fails, else `degraded` if any non-critical check fails, else `ok`.
  - `missing_components` lists the failed component names in table order.
- `main(arguments: list[str] | None = None) -> int`:
  - prints `OK` or `MISSING` + component + detail per check, then the status;
  - returns 1 when failed, 0 when ok or degraded;
  - with `--strict`, also returns 1 when degraded.

  It is runnable as `python -m medical_archive.preflight` and uses `Settings(testing=True)`, so it can run without the secret in development.

### CLI and scripts
- **`python -m medical_archive.backup.restore --backup-file PATH`**, messages in English like the bootstrap CLI:
  1. Strict `Settings()`, then `recover_backup_state`.
  2. Print a warning that the server must be stopped and that all current data and sessions will be replaced.
  3. `input()` must equal `RESTORE`, otherwise exit 1 without changes.
  4. `getpass` for the backup password.
  5. `backup_operation_lock`, copy the file into the operation directory, `stage_restore`, `apply_restore` (dispose hook is a no-op).
  6. Print `Restore completed. All sessions were signed out.`
  7. `InvalidBackup` subclasses map to `SystemExit` with English equivalents of the 422 texts; `RestoreFailedError` maps to a rolled-back message; exit code 1. No admin password is asked: shell access to the host is the trust boundary.
- **`scripts/restore-backup.ps1`**: `param([Parameter(Mandatory)][string]$BackupFile)`. Fails if `Test-Path` is false. Runs `python -m medical_archive.backup.restore --backup-file $BackupFile` and exits with `$LASTEXITCODE`.
- **`scripts/run-lan.ps1`**:
  - `param([Parameter(Mandatory)][string]$HostAddress, [int]$Port = 8443, [Parameter(Mandatory)][string]$CertFile, [Parameter(Mandatory)][string]$KeyFile)`;
  - `$ErrorActionPreference = "Stop"`;
  - fails with a Polish message if the cert or key is missing, or if `MEDICAL_ARCHIVE_SESSION_SECRET` is unset. It never generates certificates;
  - sets `MEDICAL_ARCHIVE_ENVIRONMENT=production`, `_HOST`, `_PORT`, `_TLS_CERT` and `_TLS_KEY` for the process, then runs `python -m medical_archive`;
  - Python Settings remain the authoritative validation.
- **`scripts/check-release.ps1`** runs each step in order. Any non-zero `$LASTEXITCODE` aborts with the step name and exits 1.
  1. `python -m ruff check .`
  2. `python -m ruff format --check .`
  3. `python -m pytest -q`
  4. Alembic `upgrade head` → `downgrade base` → `upgrade head`, against a new temporary directory under `[IO.Path]::GetTempPath()`. `MEDICAL_ARCHIVE_DATABASE_URL` is set to that temp DB only for these commands and restored afterwards. The temp directory is removed in `finally`.
  5. `python -m medical_archive.preflight`. `degraded` gives a warning; `failed` fails the gate.
  6. `git status --porcelain --untracked-files=all` lines matching `\.(db|sqlite|sqlite3|db-wal|db-shm|marchive|pem|key|crt|cer|p12|pfx)$` or `\.env$` fail the gate.
  7. Print `Release checks passed`.

### README runbook outline (Polish, replacing the "nie jest gotowa…" warning)
1. **Uruchomienie deweloperskie**: `python -m medical_archive` (127.0.0.1, http).
2. **Uruchomienie w sieci LAN (HTTPS)**:
   - mkcert: `mkcert -install`, then `mkcert 192.168.1.10 archiwum.lan`;
   - where to keep the key (outside the repo, restricted ACL);
   - installing the mkcert root CA on each client (Windows, Android, iOS);
   - production variables (`MEDICAL_ARCHIVE_ENVIRONMENT`, `_HOST`, `_PORT`, `_TLS_CERT`, `_TLS_KEY`, `_SESSION_SECRET`, `_ALLOWED_HOSTS='["archiwum.lan"]'`);
   - `.\scripts\run-lan.ps1`.
   - Placeholders only: `192.168.1.10`, `archiwum.lan`.
3. **Zapora Windows**: an inbound rule for the app port on the **Private** profile only, limited to the local subnet. Verify that the Public profile blocks the port.
4. **LM Studio tylko na localhost**: the server binds to 127.0.0.1. How to verify with `Get-NetTCPConnection` / `netstat`.
5. **Kopia zapasowa**:
   - what the backup contains: all profiles, with the cross-profile access warning;
   - backup password rules;
   - store the file and the password separately;
   - **always make a backup before an update or a migration**.
6. **Aktualizacja aplikacji**: backup → stop → `git pull` / `pip install -e .` → `alembic upgrade head` → `check-release.ps1` → start.
7. **Odtwarzanie przez UI**: step by step, and what happens (maintenance, everyone signed out, credentials from the backup).
8. **Odtwarzanie offline (CLI)**: stop the server → `.\scripts\restore-backup.ps1 -BackupFile …` → start. Recovery after an interrupted restore is automatic at startup; if startup refuses, keep the data directory and contact the maintainer.
9. **Weryfikacja kopii**: test restore on synthetic data on a separate data dir (plan Task 4 step 5).
10. **Utrata hasła kopii**: unrecoverable. **Utrata hasła administratora**: the bootstrap CLI or a restore of an older backup.
11. **Kontrola izolacji sieci**: from a phone on the LAN, HTTPS works; from outside the LAN the port is closed; the LM Studio port is closed on every interface except loopback.
12. **Ograniczenia**:
    - Windows has no secure delete; plaintext temporary files live briefly in `data_dir/.backup-tmp` and `.restore-staging`;
    - BitLocker is recommended;
    - the maximum backup size is 1 GiB;
    - restore needs free disk space of about 3 times the backup size.

---

## Implementation Guidance

### Suggested step groups (dependency order)
1. **Format and crypto**: `cryptography` dependency, `crypto.py`, `format.py`, `create_backup`. Gate: the two existing test files are green, unmodified.
2. **Config, engine and startup**: Settings fields and validators, `db.reset_database_engine`, `create_app` changes, `__main__.serve`, `env.py` precedence, test-env isolation fixture.
3. **Multipart and form helpers**: shared part iterator, `read_multipart_form`, `web/forms.py` move.
4. **Restore service**: staging, apply, journal, rollback, recovery, lock, maintenance state and middleware.
5. **Router and UI**: `router.py`, templates, CSS, nav link, login notice.
6. **CLI, preflight and scripts**: `backup/restore.py`, `preflight.py`, `tools/check_local_ai.py` wrapper, three scripts.
7. **Docs**: README runbook, `.gitignore`.

### Testing Approach
- 2–8 focused tests per step group. Test verification runs only the new tests of that group, plus the listed regression gates, not the entire suite. The full suite runs in `check-release.ps1`.
- Test-first, with RED confirmed. The existing backup tests are already RED (collection error).
- Use a real Alembic-migrated `tmp_path` SQLite, hand-written fakes (`FakeModelSource` for preflight), `monkeypatch` for failure injection, and synthetic data only.
- Add an autouse fixture in `tests/conftest.py` that runs `monkeypatch.delenv` for `MEDICAL_ARCHIVE_ENVIRONMENT`, `_HOST`, `_PORT`, `_TLS_CERT`, `_TLS_KEY`, `_ALLOWED_HOSTS`, `_BACKUP_MAX_BYTES` and `_DATABASE_URL`, so a developer's production shell cannot leak into tests. It does not touch the existing test files.
- New `tests/backup/conftest.py` provides:
  - `backup_data_dir` (`tmp_path/"data"`);
  - `backup_service`, a real `BackupService` with `database_url` set to the `auth_engine` URL, `dispose_engine=auth_engine.dispose` and a small configurable `max_bytes`;
  - `backup_app`, which sets `app.state.data_dir` and `app.state.backup_service`;
  - `admin_client` and `member_client` (logged in via a private `_login`);
  - a `_make_envelope` helper.

**Group 1, format and crypto** (existing tests + new `tests/backup/test_archive_format.py`)
- Existing: `test_crypto.py` (4 tests and 5 parametrized cases) and `test_roundtrip.py` (4 tests), unchanged.
- New:
  - `test_archive_validation_rejects_unlisted_missing_and_symlink_entries` (parametrized: absolute path, backslash, `C:` drive, `files/not-a-uuid`, unlisted file, missing listed file, symlink mode);
  - `test_archive_validation_rejects_entry_whose_hash_or_size_differs_from_manifest`;
  - `test_backup_refuses_export_when_a_referenced_original_is_missing`;
  - `test_backup_refuses_export_over_size_limit_before_building_archive`.

**Group 2, config and startup** (`tests/test_network_config.py`)
- The plan's `test_production_config_refuses_public_bind_and_missing_tls` and `test_lm_studio_url_must_remain_loopback`, as written.
- `test_production_rejects_every_non_private_lan_host_class`, parametrized over `0.0.0.0`, `::`, `127.0.0.1`, `::1`, `224.0.0.1`, `169.254.1.1`, `fe80::1`, `8.8.8.8`, `100.64.0.1`.
- `test_production_accepts_private_lan_host_with_existing_tls_files`.
- `test_development_rejects_non_loopback_host`.
- `test_allowed_hosts_rejects_wildcards`.
- `test_production_app_rejects_foreign_host_header_and_sends_no_cors_headers` (TrustedHost 400; no `access-control-allow-origin`; `/docs` and `/openapi.json` 404).
- `test_create_app_outside_testing_refuses_to_start_without_session_secret`.
- `test_serve_runs_uvicorn_with_tls_and_without_proxy_headers` (monkeypatch `uvicorn.run` and capture the kwargs).
- Regression gate: `tests/test_database.py`.

**Group 3, multipart** (`tests/documents/test_multipart_form.py`)
- `test_multipart_form_streams_file_to_disk_and_returns_allowed_text_fields`.
- `test_multipart_form_calls_before_file_before_writing_any_file_bytes` (the callback raises; the destination does not exist).
- `test_multipart_form_rejects_unknown_or_duplicate_text_fields`.
- `test_multipart_form_rejects_file_over_cap_and_removes_partial_file` (413).
- `test_multipart_form_rejects_oversized_content_length_before_reading_body`.
- Regression gate: `tests/documents/test_upload.py`, unchanged.

**Group 4, restore service** (`tests/backup/test_restore_safety.py`)
- `test_bad_backup_never_replaces_existing_database`: parametrized over corrupt bytes, wrong password, flipped tag, traversal archive and unsupported envelope version. Asserts `InvalidBackup`, the live DB **bytes** unchanged and the documents dir listing unchanged.
- `test_restore_rejects_path_traversal_entry_before_database_swap`.
- `test_restore_rejects_backup_without_admin_account`.
- `test_restore_rejects_newer_schema_revision`.
- `test_restore_upgrades_older_revision_only_on_staged_copy`: backup at `0004_vector_index`, with `MEDICAL_ARCHIVE_DATABASE_URL` pointing at the live DB; the live DB is unchanged after staging, and the staged DB is at head.
- `test_failed_swap_rolls_back_database_and_documents`: monkeypatch the private documents-swap step to raise `OSError`. Asserts `RestoreFailedError`, live content unchanged (`iterdump` comparison), sessions intact, no journal, no operation dir.
- `test_interrupted_restore_is_rolled_back_at_startup`: the private step raises a `BaseException` subclass, then `recover_backup_state` runs. Asserts the previous content and no leftovers.
- `test_restore_and_export_never_log_passwords` (caplog at DEBUG).

**Group 5, router and UI** (`tests/backup/test_backup_ui.py`)
- `test_member_gets_403_and_anonymous_is_redirected_from_backup_routes` (GET and both POSTs).
- `test_backup_post_without_csrf_is_forbidden_and_leaves_no_temp_files` (export and restore; the restore form has no `csrf_token` before the file).
- `test_wrong_admin_password_or_missing_confirmation_is_rejected_without_changes`, parametrized: 403 field error; 400 confirmation; export repeat mismatch 400. Asserts the exact Polish strings.
- `test_restore_with_corrupt_or_wrong_password_backup_returns_identical_422_and_keeps_live_data`.
- `test_restore_over_size_cap_returns_413` and `test_concurrent_backup_operation_returns_409` (hold `backup_operation_lock` inside the test).
- `test_export_downloads_no_store_attachment_and_removes_server_copy`.
- `test_full_backup_restore_roundtrip_returns_accounts_grants_documents_vectors_and_measurements` (acceptance 6):
  - seed an admin, a member, a grant, a document with a page and an original file, a vec0 row and a measurement;
  - export over HTTP, then mutate the live store;
  - restore over HTTP. Expect a 303 to `/auth/login?restored=1` and every session wiped (the old cookie redirects to login);
  - the login page shows the fixed notice;
  - the original state is back, byte-equal for the file.
- `test_maintenance_mode_returns_503_for_everything_except_health` (state active: `/`, `/static/app.css` and `/auth/login` return 503 with `Retry-After`; `/health` returns 200).
- `test_login_notice_ignores_unexpected_restored_values` (`?restored=<script>` shows no notice and nothing is reflected).

**Group 6, CLI, preflight and release** (`tests/test_release_guards.py`, `tests/backup/test_restore_cli.py`)
- The plan's `test_production_start_refuses_missing_tls_configuration(settings_factory)`, with a local `settings_factory` fixture that builds `Settings(data_dir=tmp_path, **overrides)`.
- The plan's `test_preflight_reports_missing_local_ai_without_hiding_the_degraded_state(ai_preflight)`, with a local `ai_preflight` fixture: `Preflight(settings=<testing settings on the migrated auth_engine DB>, fetch_model_ids=lambda: [])`.
- `test_preflight_fails_when_sqlite_vec_is_unavailable` (monkeypatch `load_sqlite_vec`).
- `test_preflight_main_strict_mode_fails_on_degraded`.
- `test_restore_cli_requires_typed_confirmation_and_restores_backup` (monkeypatch `input` and `getpass`).

### Standards Compliance
- `security/access-control.md`: admin-only routes enforced server-side (`require_admin`); the nav link is not access control; UI and README warn about cross-profile access before export and restore.
- `security/authentication.md`:
  - CSRF on both POSTs (restore checks it before the file streams);
  - rotation on every render;
  - `hmac.compare_digest` for the repeat check;
  - strict form parsing (413, 415, 400 duplicates);
  - Secure, HttpOnly, SameSite=Strict cookie deleted after restore.
- `security/data-protection.md`:
  - event-style logs with opaque ids only (`backup_*`, `archive_id`, `operation_id`, `code`, `duration_ms`); never passwords, filenames or paths;
  - streamed upload to disk;
  - plaintext only under `data_dir` and deleted;
  - LAN-only HTTPS, loopback LM Studio, no CORS, restricted Host;
  - fully verified, non-destructive restore.
- `backend/configuration.md`: a single `Settings` class; bounded fields; actionable validators; fail-closed production profile (DB, sqlite-vec, secret, TLS, private LAN bind).
- `backend/api.md`: `Annotated[..., Depends]`; collaborators on `app.state`; HTML routes `include_in_schema=False`; 303 after a successful form POST; OpenAPI disabled.
- `backend/migrations.md`: no new migration. The `env.py` precedence change is documented: the programmatic URL wins.
- `global/error-handling.md`: typed `*Error` exceptions translated at the router with `raise … from error`; explicit degraded state in preflight; no silent fallbacks (sqlite-vec missing gives a failure); cleanup in `finally`.
- `global/minimal-implementation.md`: no offline export, no KDF injection, no JSON maintenance variant, no last-backup hint; every new function has a caller.
- `global/coding-style.md` and `conventions.md`: absolute imports; no `from __future__ import annotations` in `src` (remove it from `tools/check_local_ai.py` only if it is rewritten as the wrapper); keyword-only options; frozen dataclasses; English code with Polish UI; Ruff line length 100.
- `frontend/*`: extends `base.html` with `section.panel`; `<label for>` everywhere; one `role=alert`; `aria-invalid` and `aria-describedby`; no JS; local `app.css` only; rem units; mobile-first (file input `max-width:100%`); contrast pairs ≥ 4.5:1.
- `testing/test-writing.md`: behaviour-named module-level tests; RED first; fakes and monkeypatch instead of a mock library; the required negative backup cases (wrong password, corruption, no destructive overwrite).

---

## Acceptance Criteria Mapping

| Criterion | Covered by |
|---|---|
| **Design-spec 6**: an encrypted backup restores to a consistent state | `test_full_backup_restore_roundtrip_…`, `test_backup_roundtrip_includes_database_wal_and_original_files`, the health check in phase 2, `test_restore_upgrades_older_revision_only_on_staged_copy` |
| **Design-spec 6**: a wrong password or corruption is detected without overwriting | `test_crypto.py` (wrong password, tag, header), `test_bad_backup_never_replaces_existing_database`, `test_restore_with_corrupt_or_wrong_password_backup_returns_identical_422_…`, `test_failed_swap_rolls_back_…`, `test_interrupted_restore_is_rolled_back_at_startup` |
| **Design-spec 7**: no data sent externally | preflight probes only loopback LM Studio and sends no documents; no CDN or telemetry added; `test_restore_and_export_never_log_passwords` |
| **Design-spec 7**: model service not reachable from the LAN | `test_lm_studio_url_must_remain_loopback`; README firewall and LM Studio localhost check; manual isolation check |
| **Design-spec 7**: app reachable only on the trusted LAN over HTTPS | `test_production_rejects_every_non_private_lan_host_class`, `test_production_config_refuses_public_bind_and_missing_tls`, `test_production_app_rejects_foreign_host_header_…`, `test_serve_runs_uvicorn_with_tls_…` |
| **Plan Task 1** (format and roundtrip) | Group 1 |
| **Plan Task 2** (safe restore and UI) | Groups 3, 4, 5 |
| **Plan Task 3** (TLS and LAN bind) | Group 2, `run-lan.ps1`, README sections 2–4 |
| **Plan Task 4** (release checks and runbook) | Group 6, `check-release.ps1`, `restore-backup.ps1`, README sections 5–12, manual LAN and phone check and synthetic-data restore drill |

## Success Criteria
1. `py -3.12 -m pytest -q` passes, including the two **unmodified** existing backup test files and every new test listed above.
2. `ruff check .` and `ruff format --check .` are clean.
3. `.\scripts\check-release.ps1` exits 0 on the target machine. A `degraded` preflight is a warning only.
4. With an invalid production config (missing TLS, `0.0.0.0`, public IP, missing secret, remote LM Studio), `python -m medical_archive` refuses to start and the message names the offending fields.
5. Every rejected restore leaves the live DB byte-identical and `documents/` unchanged. A successful restore reproduces the backup's accounts, grants, documents, pages, vectors, measurements and file bytes, with zero sessions.
6. No plaintext backup material remains under `data_dir/.backup-tmp` or `.restore-staging` after any completed, failed or recovered operation.
7. Manual check: a phone on the LAN opens HTTPS without a certificate warning (mkcert CA installed); the port is closed from outside the LAN; the LM Studio port is closed except on loopback.
8. `git status --short` shows no DB, backup, certificate, key or `.env` files.

---

## Open Assumptions
1. **Active admin** means a row with `users.is_admin = 1`. The schema has no disabled or active flag.
2. The scrypt parameters are not injectable. Tests pay about 0.1 s per KDF call, which is acceptable for about 15 KDF-using tests.
3. Backup passwords are encoded as UTF-8 without Unicode normalisation, the same as account passwords.
4. `backup_max_bytes` can only be lowered (ceiling 1 GiB), because the v1 one-shot envelope holds the archive in memory, with a peak of about 2–3 times the archive size during restore.
5. The CLI restore relies on the operator stopping the server. There is no cross-process lock. The CLI warns and requires typed confirmation.
6. The live DB uses the default rollback journal (`db.py` sets no WAL). If a backup-API copy into a WAL destination with a different page size fails, the restore rolls back.
7. IPv4 private addresses are the documented path. IPv6 ULA passes validation, but TrustedHost with IPv6 literals is not specifically tested.
8. Unreferenced orphan files in `documents/` (left by failed uploads) are not backed up and are discarded after a successful restore.
9. FastAPI 0.143 / Starlette 1.8: the router explicitly commits its request session before phase 2, so no pooled connection stays checked out, whatever the timing of the yield-dependency teardown.
10. The drain timeout is 30 s and the maintenance `Retry-After` is 30 s (constants, not settings).
11. The login page gains the `app.css` link, so the notice is styled. This changes the look of the login page slightly, in line with the rest of the app.
12. `documents/service.py` keeps its `Settings(testing=True)` reads (see the module map rationale).

## Known Limitations
- No secure delete on Windows (documented in the README).
- One-shot envelope: archives over 1 GiB are not supported. A streamed v2 format is future work.
- There is no offline export. If the web UI breaks before any backup exists, data recovery relies on host-level copies.
