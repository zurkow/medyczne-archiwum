# Documentation Index

**IMPORTANT**: Read this file at the beginning of any development task to understand available documentation and standards.

## Quick Reference

### Project Documentation
Project-level documentation covering vision, goals, architecture, and technology choices.

### Technical Standards
Coding standards, conventions, and best practices organized by domain: global, frontend, backend, security, ai-assistant, testing.

---

## Project Documentation

Located in `.maister/docs/project/`

### Vision (`project/vision.md`)
Pitch for Medyczne Archiwum (private, LAN-only household medical archive with a local source-grounded AI assistant), the problem of scattered sensitive records, target users (household members, administrator without access to others' data), key features (accounts and grants, PDF/photo import, Polish OCR, manual review, measurements, Bielik assistant with citations, encrypted backup), explicit out-of-scope items, acceptance-based success criteria, differentiators (fully local, ACL before vector search, honest assistant, Polish-first) and current focus (plan 4: backup and LAN).

### Roadmap (`project/roadmap.md`)
Phases mapped to `docs/superpowers/plans/`: Phase 1 foundation and accounts (done), Phase 2 documents/OCR/RAG (done, live end-to-end check pending), Phase 3 measurements and dashboard (done), Phase 4 encrypted backup/restore, TLS and LAN-only binding, release checks (current focus); technical debt (lock file, stricter Ruff/typing, CI, streamed uploads, housekeeping) and future enhancements (assistant tuning, ACL coverage, ADR folder), with S/M/L effort scale.

### Tech Stack (`project/tech-stack.md`)
Python 3.12, FastAPI + Uvicorn, Jinja2 server-rendered Polish UI with plain CSS and vanilla JS/SVG, pydantic-settings config (`MEDICAL_ARCHIVE_*`), argon2-cffi, SQLite with SQLAlchemy 2 and Alembic, sqlite-vec (768-dim cosine, user-partitioned), LM Studio on loopback (Bielik 1.5B v3.0 Instruct, nomic-embed-text v1.5), PyMuPDF and Tesseract `pol`, setuptools/pip extras (`dev`, `local-ai`), pytest with injected fakes, Ruff (line length 100); no CI, containers, type checker or lock file; Conventional Commits.

### Architecture (`project/architecture.md`)
Single-process modular monolith by domain (auth, documents, measurements, rag, web, planned backup) with services holding business rules and access checks, routers for HTTP, and dependency-injected infrastructure; component diagram, per-module locations and key files, data flows for document import (validate, store under UUID, extract/OCR, owner review, index) and assistant questions (resolve visible sources, partitioned KNN, untrusted-content prompt, JSON-schema answer, citation validation), plus external integrations (loopback LM Studio, Tesseract, PyMuPDF), models and Alembic migration chain (0001-0005), `MEDICAL_ARCHIVE_` configuration and single-Windows-host deployment with planned HTTPS/LAN-only binding.

---

## Technical Standards

### Global Standards

Located in `.maister/docs/standards/global/`

#### Coding Style (`standards/global/coding-style.md`)
Naming consistency, Ruff formatting (line length 100), descriptive names, focused functions, no dead code, no backward-compatibility shims unless required, DRY; absolute grouped `medical_archive` imports (no relative imports, no `from __future__ import annotations`), full modern type hints with keyword-only options after `*` and injectable tz-aware `now`, `_`-prefixed private helpers, frozen dataclasses for value objects and Pydantic `extra="forbid"` only for request validation.

#### Commenting (`standards/global/commenting.md`)
Let code speak for itself, comment sparingly (explain why, not what), no change-history comments in code.

#### Conventions (`standards/global/conventions.md`)
Predictable structure, README kept current, env-var configuration, lean dependencies, reviews, feature flags, build only what is needed; Python 3.12 only on Windows/PowerShell, mandatory quality gates (`pytest -q`, `ruff check`, `ruff format --check`), domain package layout (`models.py`/`service.py`/`router.py` under `src/medical_archive/<domain>/`, routers wired only in `create_app()`), English code with Polish UI, fix the environment instead of dropping tests, never commit secrets/real data/SQLite files/backups, one scoped Conventional Commit per task, README documents setup and limits, minimal pins with `dev`/`local-ai` extras.

#### Error Handling (`standards/global/error-handling.md`)
Clear safe user messages, fail fast, typed exceptions, router-boundary handling, explicit (never silent) degradation, no automatic generation retries, resource cleanup; per-domain `*Error` classes translated to `HTTPException` with `raise ... from error`, no silent fallbacks or fake success for missing components (sqlite-vec, Tesseract, LM Studio), transactional or safely ordered state changes (atomic invitation consumption and reindexing).

#### Minimal Implementation (`standards/global/minimal-implementation.md`)
Build only what you need, every piece of code has a clear purpose, delete exploration artifacts, no future stubs, no speculative abstractions, review before commit, unused code is debt.

#### Validation (`standards/global/validation.md`)
Always validate server-side, client-side validation for feedback only, validate early, specific error messages, allowlists over blocklists, type and format checks, input sanitization, business rule validation, consistent enforcement across entry points.

### Frontend Standards

Located in `.maister/docs/standards/frontend/`

#### Accessibility (`standards/frontend/accessibility.md`)
Semantic HTML, keyboard navigation, 4.5:1 contrast, alt text and labels, screen reader testing, ARIA when needed, heading structure, focus order; JavaScript-free plain HTML forms with `<label for>`, `role="alert"` errors, `aria-label`/`aria-current` navigation, table captions and `th scope`, mobile-first layout with `:focus-visible`, server-rendered inline SVG charts with a hidden description and table fallback.

#### Components (`standards/frontend/components.md`)
Single-responsibility, composable Jinja partials with documented context; authenticated pages extend `base.html` with a `section.panel` and matching `h1`, standalone `lang="pl"` pre-auth pages, rendering only via `render_template` with a `title`, no frontend framework, and only already-authorized data in rendered HTML/SVG.

#### CSS (`standards/frontend/css.md`)
Plain CSS with kebab-case semantic classes and no framework, design tokens, minimal custom CSS; a single local `/static/app.css` with no CDNs, web fonts or external URLs, system-ui font stack, `rem` sizes.

#### Responsive Design (`standards/frontend/responsive.md`)
Mobile-first, standard breakpoints, fluid layouts, relative units, cross-device testing, touch-friendly targets, mobile performance, readable typography, content priority.

### Backend Standards

Located in `.maister/docs/standards/backend/`

#### API Design (`standards/backend/api.md`)
Resource-based URLs, consistent naming, no URL versioning, plural nouns, limited nesting, query parameters, proper status codes (404 for hidden resources); `Annotated[T, Depends(fn)]` dependencies and dependency/router factories with swappable collaborators on `app.state`, JSON endpoints at the collection root plus `/ui` HTML pages and POST form actions with 303 redirects, `resource_id` path parameter, 201/202/204 conventions, OpenAPI disabled, explicit allow-listed `public_<entity>()` serializers with UTC `Z` timestamps.

#### Configuration (`standards/backend/configuration.md`)
Single pydantic-settings `Settings` class (`MEDICAL_ARCHIVE_` prefix) validated at startup with bounded fields and actionable validators, no default secrets (mandatory 32-byte session secret outside testing), first admin only via the bootstrap command; planned fail-closed production profile (required DB, sqlite-vec, secret, TLS; explicit private LAN bind address).

#### Domain Rules (`standards/backend/domain-rules.md`)
Owner-approved document lifecycle (`needs_review -> indexing -> ready`, `ocr_failed`/`index_failed`, non-authoritative OCR suggestions, only `ready` documents listed/indexed/shared, owner-only actions, originals survive failures); Decimal measurements stored as `Numeric(20, 8)` with UTC times, rejection of NaN/infinite/naive values, bounded paginated stable-sorted lists, no unit conversion.

#### Migrations (`standards/backend/migrations.md`)
Reversible, small, focused, descriptively named migrations kept in version control and never edited after being applied; hand-written Alembic files `NNNN_<description>.py` with revision equal to the filename stem, explicit `down_revision`, `upgrade`/`downgrade` (indexes dropped before tables), ORM-mirroring columns, must survive `downgrade base` -> `upgrade head`; database URL resolved from `MEDICAL_ARCHIVE_DATABASE_URL` in `migrations/env.py`.

#### Models (`standards/backend/models.md`)
Plural snake_case tables, `created_at` timestamps, DB-level constraints, appropriate types, indexed foreign keys, multi-layer validation, clear relationships; SQLite-only SQLAlchemy 2 typed models (`Mapped`/`mapped_column`) with `PRAGMA foreign_keys=ON`, cascading `owner_user_id` FKs, tz-aware `created_at` server default, UUID4 `String(36)` keys for user-owned resources, `ix_`/`ck_`/`uq_` naming, string statuses with CHECK constraints.

#### Queries (`standards/backend/queries.md`)
Parameterized queries, avoid N+1, select only needed columns, index strategic columns, transactions for related writes, query timeouts, cache expensive queries.

### Security Standards

Located in `.maister/docs/standards/security/`

#### Access Control (`standards/security/access-control.md`)
Identity and permission checks in the domain service on every endpoint (grants helpers and `require_resource_access`/`require_resource_owner` factories, owner IDs never from the client, no admin bypass, owner-only share/revoke, read-only grantees, immediate revocation); 404 "Resource not found." instead of 403 for hidden resources, including for admins; administrators never see other users' data except via the warned-about encrypted full backup.

#### Authentication & Request Integrity (`standards/security/authentication.md`)
Argon2id password hashing, `token_urlsafe(32)` session/invitation tokens stored only as SHA256, single-use time-limited invitations, `HttpOnly`/`Secure`/`SameSite=Strict` 12 h cookie, uniform login failure message; synchronizer CSRF tokens (hidden `csrf_token` or `X-CSRF-Token`, `hmac.compare_digest`, 403 on failure, rotation on GET and login, scoped pre-auth sessions); strict manual form parsing (byte cap 413, content type 415, field limits, duplicate fields 400).

#### Data Protection (`standards/security/data-protection.md`)
Event-style logs with only opaque IDs, codes and durations (never document text, OCR output, filenames, prompts, values or passwords); private UUID-keyed atomic file storage outside the webroot served through permission checks, streamed size validation and signature checks (current in-memory buffering is tech debt); local-only data flow (no telemetry/CDNs, loopback-only LM Studio endpoints, LAN-only HTTPS, no wildcard CORS); planned AES-256-GCM/scrypt backups with fully verified, non-destructive atomic restore.

### AI Assistant Standards

Located in `.maister/docs/standards/ai-assistant/`

#### Answer Contract (`standards/ai-assistant/answers.md`)
Model-cited IDs must be a subset of prompt sources and displayed citations are built from the DB by the app; document/OCR/note content is untrusted input framed as quoted material; Polish answers only from retrieved context, explicit no-data below the relevance threshold without calling the model, explicit errors on invalid JSON/foreign IDs/missing model/timeout, no generation retries; no diagnosis, urgency, treatment advice, unit conversion or clinical trend interpretation.

#### Retrieval & Indexing (`standards/ai-assistant/retrieval.md`)
Access filter before retrieval with the user's sqlite-vec partition key passed to KNN (never post-filtered), chunk IDs rechecked against visible documents, revocation removes grantee index entries; measurements fetched by ACL-limited 365-day SQL instead of vectors with typed `measurement` citations; validated 768-dim finite embeddings, ~500-unit chunks with overlap 80 preserving page boundaries, strict JSON Schema `response_format`.

### Testing Standards

Located in `.maister/docs/standards/testing/`

#### Test Writing (`standards/testing/test-writing.md`)
Behaviour-focused, fast, risk-based tests; test-first with confirmed RED; hand-written `Fake*` collaborators injected via constructors or `app.state` (no mock library, no network/model calls); isolated Alembic-migrated `tmp_path` SQLite with shared conftest fixtures (`app`, `client`, `admin_user`, `member_user`, `extract_csrf_token`); module-level `test_<behaviour_sentence>` functions with required negative security cases (cross-account leaks, grants/revocation, 404 for admin and non-owner, OCR/assistant/backup failures); plain asserts, `pytest.raises`, `parametrize`, synthetic data; prove risky native dependencies with a compatibility test first.

---

## How to Use This Documentation

1. **Start Here**: Always read this INDEX.md first to understand what documentation exists
2. **Project Context**: Read relevant project documentation before starting work
3. **Standards**: Reference appropriate standards when writing code
4. **Keep Updated**: Update documentation when making significant changes
5. **Customize**: Adapt all documentation to your project's specific needs

## Updating Documentation

- Project documentation should be updated when goals, tech stack, or architecture changes
- Technical standards should be updated when team conventions evolve
- Always update INDEX.md when adding, removing, or significantly changing documentation
