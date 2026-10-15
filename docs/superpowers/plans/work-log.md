# Work Log

## 2026-10-13 - Implementation Started

**Total task groups**: 16
**Execution mode**: Sequential task groups with delegated implementation.
**Plan sources**:
- `2026-10-13-fundament-i-konta.md`
- `2026-10-13-dokumenty-ocr-rag.md`
- `2026-10-13-pomiary-i-pulpit.md`
- `2026-10-13-backup-i-lan.md`

## Standards Reading Log

The implementation repository does not contain `.maister/docs/`. The available index and shared standards were read from `C:\Users\piotr\IdeaProjects\ha-config\.maister\docs\`; project-specific architecture and stack notes there describe a different repository and are not applied.

### Group 1: Application bootstrap and test foundation
**From implementation plan**:
- [x] `.maister/docs/standards/global/coding-style.md` — focused modules, descriptive naming, automatic formatting.
- [x] `.maister/docs/standards/global/conventions.md` — predictable structure, minimal dependencies, up-to-date README.
- [x] `.maister/docs/standards/global/minimal-implementation.md` — avoid speculative scaffolding.
- [x] `.maister/docs/standards/testing/test-writing.md` — behavior-oriented, fast tests.

**From INDEX.md**:
- [x] `.maister/docs/INDEX.md` — checked for project-specific documentation and applicable standards.

**Discovered during execution**:
- [x] `pyproject.toml` Ruff exclusion for generated documentation — format checking found existing plan documents outside the code scope; exclude `docs` rather than reformatting unrelated approved documentation.
- [x] `sqlite-vec` 0.1.9 requires `connection.enable_load_extension(True)` before `sqlite_vec.load(connection)` in the Windows compatibility test.

## 2026-10-13 - Group 1 Complete

**Steps**: 1.1 through 1.6 completed.
**Standards applied**:
- From plan: bootstrap scope, package metadata, health endpoint, test configuration and medical-data warning.
- From INDEX.md: coding style, conventions, minimal implementation, version-control hygiene and behavior-focused testing.
- Discovered: exclude documentation from Ruff formatting checks to avoid changing unrelated plan documents.
**Tests**: health test 1 passed; Ruff lint and format checks passed.
**Files modified**: `.gitignore`, `README.md`, `pyproject.toml`, `src/medical_archive/__init__.py`, `src/medical_archive/main.py`, `tests/conftest.py`, `tests/test_health.py`.
**Commit**: `1dbc5b6` (`build: bootstrap medical archive app`).
**Notes**: Python 3.12 tooling was not initially available on PATH; the implementer installed/located it and completed checks. The Starlette/httpx deprecation warning is non-blocking.

### Group 2: SQLite configuration and identity schema
**From implementation plan**:
- [x] Task 2 requirements — settings, SQLite session factory, identity schema and reversible Alembic migration.

**From INDEX.md**:
- [x] `standards/global/coding-style.md`
- [x] `standards/global/validation.md`
- [x] `standards/global/error-handling.md`
- [x] `standards/global/minimal-implementation.md`
- [x] `standards/global/conventions.md`
- [x] `standards/global/version-control.md`
- [x] `standards/testing/test-writing.md`

**Discovered during execution**:
- [x] Clear `MEDICAL_ARCHIVE_DATABASE_URL` while exercising the fixture's isolated migration DB so a developer override cannot redirect test setup.

### Group 2 Complete
**Steps**: 2.1 through 2.6 completed.
**Tests**: database/config tests 2 passed; migration upgrade/downgrade/upgrade passed.
**Files modified**: identity configuration, models, Alembic setup/migration and `tests/test_database.py`.
**Commit**: `ed04591` (`feat: add sqlite identity schema`).
**Notes**: focused Ruff checks passed. A concurrent repository-wide Ruff run during the next group reported transient in-flight findings; after both groups finished, repository-wide Ruff lint and format checks passed.

### Group 5 (Documents/RAG): sqlite-vec compatibility
**From implementation plan**:
- [x] Task 1 test-first gate — exercised real partition filtering inside the KNN query before continuing.

**From INDEX.md**:
- [x] `standards/global/coding-style.md`
- [x] `standards/global/validation.md`
- [x] `standards/global/error-handling.md`
- [x] `standards/global/minimal-implementation.md`
- [x] `standards/global/conventions.md`
- [x] `standards/global/version-control.md`
- [x] `standards/testing/test-writing.md`

**Discovered during execution**:
- [x] Enable SQLite extension loading explicitly before calling `sqlite_vec.load`.

**Steps**: 1.1 through 1.5 completed.
**Tests**: real partitioned-KNN compatibility test 1 passed; local diagnostic reports missing Tesseract/Polish data and LM Studio with a nonzero status; task-scoped Ruff checks passed.
**Files modified**: `tests/rag/test_sqlite_vec_compatibility.py`, `tools/check_local_ai.py`, `pyproject.toml`, `README.md`.
**Commit**: `25eecf5` (`test: verify local vector search support`).
**Notes**: local LM Studio and Tesseract are not installed. This is explicitly reported as incomplete local AI setup; the app's other tests do not need those services.

### Group 3: Authentication, sessions and invitations
**From implementation plan**:
- [x] Task 3 requirements — Argon2id, opaque session/invitation tokens, secure cookies, CSRF, first-admin bootstrap, invitations and registration.
- [x] Task 3 clarification — pre-auth session purposes are isolated for login vs registration and never authorize protected routes.

**From INDEX.md**:
- [x] `standards/global/coding-style.md`
- [x] `standards/global/validation.md`
- [x] `standards/global/error-handling.md`
- [x] `standards/global/conventions.md`
- [x] `standards/global/minimal-implementation.md`
- [x] `standards/global/version-control.md`
- [x] `standards/testing/test-writing.md`

**Discovered during execution**:
- [x] Shared repositories can receive concurrent commits; pause and obtain user direction before adopting an unexpected in-scope commit.

**Steps**: 3.1, 3.3, 3.4, 3.5 and 3.6 verified. Step 3.2 (pre-implementation RED) was not independently verifiable because the accepted authentication commit was already present.
**Tests**: authentication tests 9 passed; full suite 13 passed; Ruff lint and format checks passed.
**Files modified by accepted commit**: authentication/password/session/invitation services and templates, web/auth routers, migration `0002_authentication_sessions.py`, tests and fixtures.
**Commit**: `75fd2e0` (`feat: add secure account sessions`), already present on `main` when reviewed; user explicitly approved reviewing and continuing from it.
**Notes**: pre-auth `registration` sessions are purpose-scoped separately from login and excluded from authenticated dependencies. The shared commit was reviewed in place, not modified or recreated.

### Group 4: Explicit resource access grants
**From implementation plan**:
- [x] Task 4 ACL contract — domain-supplied owner checks, no administrator bypass, owner-only sharing/revocation and 404 for hidden resources.
- [x] Task 4 UI — available-module navigation, Polish labels, focus indication and contrast.

**From INDEX.md**:
- [x] `standards/global/coding-style.md`
- [x] `standards/global/validation.md`
- [x] `standards/global/error-handling.md`
- [x] `standards/global/minimal-implementation.md`
- [x] `standards/global/conventions.md`
- [x] `standards/global/version-control.md`
- [x] `standards/testing/test-writing.md`

**Discovered during execution**:
- [x] Domain ownership is not derivable from a generic grants table; callers must pass IDs from trusted owner queries into `visible_resource_ids`.

**Steps**: 4.1 through 4.6 completed.
**Tests**: authorization/authentication tests 12 passed; full suite 16 passed; Ruff lint and format checks passed.
**Files modified**: grant policy and router factory, resource access dependencies, main/static mount, base template, CSS, README and authorization tests.
**Commit**: `e73302d` (`feat: enforce explicit resource access`).
**Notes**: the grant-router factory is not mounted until domain routers are available. Subsequent document and measurement plans must mount it with trusted owner lookups and exercise the actual HTTP routes.

### Group 6: Private document upload and storage
**From implementation plan**:
- [x] Task 2 requirements — bounded streaming upload, strict signature/MIME/extension validation, UUID storage key, private path, ACL and ready-only sharing.

**From INDEX.md**:
- [x] `standards/global/coding-style.md`
- [x] `standards/global/validation.md`
- [x] `standards/global/error-handling.md`
- [x] `standards/global/minimal-implementation.md`
- [x] `standards/global/version-control.md`
- [x] `standards/testing/test-writing.md`

**Discovered during execution**:
- [x] Access-route dependencies require path parameter `resource_id`; document detail/download routes use that exact identifier.

**Steps**: 2.1 through 2.5 completed.
**Tests**: expected RED (4 upload tests failed with 404 before implementation); focused upload/access tests 8 passed; full suite 24 passed; Ruff lint/format and migration upgrade/downgrade/upgrade passed.
**Files modified**: documents model/storage/service/router, migration `0002_documents.py`, main/model exports, upload/access tests.
**Commit**: `7d202dd` (`feat: add private document uploads`).
**Notes**: local upload parser is bounded at 25 MiB but buffers the accepted file in memory before storage. `python-multipart` was not available and lies outside the plan's declared file/dependency scope; revisit disk-spooling multipart handling if concurrent upload memory use becomes material. No OCR was started in this group.

### Group 7: OCR and owner review workflow
**From implementation plan**:
- [x] Task 3 extraction/review contracts — local text/OCR extraction, non-authoritative metadata, owner-only approval, failure retention, and injected indexer.

**From INDEX.md**:
- [x] `standards/global/coding-style.md`
- [x] `standards/global/validation.md`
- [x] `standards/global/error-handling.md`
- [x] `standards/global/minimal-implementation.md`
- [x] `standards/global/version-control.md`
- [x] `standards/testing/test-writing.md`

**Discovered during execution**:
- [x] Existing FastAPI/SQLAlchemy/Jinja2 patterns — preserve safe HTTP error handling and ensure user-submitted metadata is revalidated server-side.

**Steps**: 3.1 through 3.5 completed.
**Tests**: expected RED before extraction/review code; document tests 22 passed; full suite 38 passed; migration upgrade/downgrade/upgrade passed; Ruff lint and formatting checks passed.
**Files modified**: extraction, metadata suggestions, review/indexer contract, document model/service/router, migration `0003_document_text.py`, templates and OCR/approval tests.
**Commit**: `5779037` (`feat: add local document text extraction`).
**Notes**: Tesseract and Polish language data are not installed locally; adapter tests cover explicit missing-dependency errors, and product code retains the original for retry.

### Group 8: Retrieval, Bielik and verified citations
**Plan**: Task 5 from `2026-10-13-dokumenty-ocr-rag.md`.
**Execution note**: The Task 5 implementation and tests were already present in the worktree when execution began. The RED baseline was not reproduced; existing work was preserved and reviewed rather than removed to manufacture a failing state.
**Standards Applied**:
- From plan: authorized retrieval, explicit no-data, validated citations, loopback-only LM Studio, and no measurement retrieval in this group.
- From shared INDEX.md: global coding style, validation, error handling, minimal implementation, conventions, version control, and test-writing standards.
**Tests**: focused assistant tests 8 passed; RAG/document tests 41 passed; full suite 56 passed. Ruff check and format check passed (`58 files already formatted`); `git diff --check` passed.
**Files in Task 5 commit**: `src/medical_archive/config.py`, `src/medical_archive/documents/service.py`, `src/medical_archive/main.py`, `src/medical_archive/rag/prompt.py`, `src/medical_archive/rag/retrieval.py`, `src/medical_archive/rag/router.py`, `src/medical_archive/rag/service.py`, `src/medical_archive/templates/chat.html`, `tests/rag/test_answer.py`, `tests/rag/test_retrieval.py`.
**Live verification**: `tools/check_local_ai.py` reported no LM Studio API at `127.0.0.1:1234`, no Tesseract executable, and no verifiable Polish language data. No model or application service was started or stopped. Manual end-to-end verification remains outstanding.
**Commit**: `cfe10db` (`feat: add source-grounded local assistant`); Copilot co-author trailer included.

### Group 8 follow-up: Bionic model IDs and structured output
**Changes**: Updated application defaults and README to match the user's Bionic model IDs. A live call exposed Bionic's `400` response to `response_format.type=json_object`; its API requires `json_schema` or `text`. Added a strict JSON Schema response format and a regression test.
**RED/GREEN**: The new test failed first because the client sent `json_object`, then passed after switching to `json_schema`.
**Live verification**: The embedding endpoint returned a validated 768-dimensional vector using `text-embedding-nomic-embed-text-v1.5`; the Bielik chat endpoint returned structured JSON using `bielik-1.5b-v3.0-instruct`. The diagnostic confirms both models at `127.0.0.1:1234`; Tesseract and Polish language data remain unavailable.
**End-to-end verification**: Generated a synthetic, non-identifying PDF in a temporary directory, extracted its text with PyMuPDF, indexed it using the live embedding endpoint, retrieved it under a temporary SQLite DB, and received one validated citation in the Bielik answer. All temporary test data was isolated from the repository.
**Tests**: full suite 58 passed; Ruff check and format check passed; `git diff --check` passed.
**Files**: `src/medical_archive/config.py`, `src/medical_archive/rag/service.py`, `tests/test_database.py`, `tests/rag/test_answer.py`, `README.md`.
**Commit**: `1493af0` (`fix: match Bionic models and JSON schema API`); Copilot co-author trailer included.

### Group 9: Private measurement records
**Plan**: Task 1 from `2026-10-13-pomiary-i-pulpit.md`.
**Steps**: 1.1–1.5 completed; RED confirmed with 15 expected missing-endpoint failures.
**Standards Applied**: shared global coding style, validation, error handling, minimal implementation, conventions, version control, and testing standards. Project has no local `.maister/docs/INDEX.md`.
**Tests**: focused validation/access tests 17 passed; full suite 75 passed; Ruff check and format check passed (`66 files already formatted`); migration upgrade/downgrade/upgrade passed; diff checks passed.
**Files**: measurement model/service/router, migration `0005_measurements.py`, app/model wiring, and validation/access tests.
**Commit**: `33a6bb1` (`feat: add private measurements`); Copilot co-author trailer included.

### Group 10: Measurement history, SVG, and patient dashboard
**Plan**: Task 2 from `2026-10-13-pomiary-i-pulpit.md`.
**Steps**: 2.1–2.5 completed; RED confirmed with 7 expected missing-behavior failures.
**Standards Applied**: applicable shared standards for coding style, conventions, explicit errors, minimal implementation, validation, testing, and version control. Existing project styles and accessible server-rendered UI patterns were followed; no external assets/CDN added.
**Visual reference**: `medical-design/content/dashboard-layout.html` — patient dashboard structure and mobile/focus behavior implemented. The mockup’s “+ Dodaj dokument” action remains an “Otwórz dokumenty” link because the upload UI is outside this task scope.
**Tests**: focused trend/dashboard tests 8 passed; full suite 83 passed; Ruff and formatting passed (`69 files already formatted`); migration cycle and diff checks passed.
**Files**: measurement router/trends, web router, templates, CSS, and dashboard/trend tests.
**Commit**: `624f509` (`feat: add patient overview dashboard`), fast-forwarded onto `main` after user approval; Copilot co-author trailer included.

### Group 11: Measurements as cited assistant sources
**Plan**: Task 3 from `2026-10-13-pomiary-i-pulpit.md`.
**Steps**: 3.1–3.6 completed; RED reproduced for the missing owned/shared measurement retrieval behavior.
**Standards Applied**: shared global coding style, validation, error handling, minimal implementation, conventions, version control, and test-writing standards.
**Tests**: focused measurement source tests 5 passed; RAG + measurements 48 passed; full suite 88 passed; Ruff check/format and diff checks passed.
**Files**: RAG retrieval/prompt/service/router, chat citation template, measurement-source tests, README.
**Commit**: `cd9461b` (`feat: cite measurements in assistant answers`) on `main`; Copilot co-author trailer included.
