# Development Roadmap

This roadmap outlines the development phases for **Medyczne Archiwum**. Phases map to the dependency-ordered implementation plans in `docs/superpowers/plans/`.

## Phase 1: Foundation & Accounts — done
Plan: `2026-10-13-fundament-i-konta.md`

- [x] **App bootstrap** — FastAPI app factory, validated config (`MEDICAL_ARCHIVE_*`), SQLite + Alembic `S`
- [x] **Accounts & sessions** — Argon2id passwords, hashed opaque session tokens, secure cookies, CSRF, admin bootstrap CLI `M`
- [x] **Invitations** — one-time, hashed, time-limited invitation tokens `S`
- [x] **Explicit grants** — owner-only sharing/revocation, 404 for hidden resources, no admin bypass `M`

## Phase 2: Documents, OCR & RAG — done (live verification pending)
Plan: `2026-10-13-dokumenty-ocr-rag.md`

- [x] **sqlite-vec compatibility check** — partitioned KNN on Windows/Python 3.12 `S`
- [x] **Private uploads** — bounded size, signature/MIME validation, UUID storage outside static `M`
- [x] **Extraction & review** — PDF text layer / local OCR, owner-only approval of type and date `M`
- [x] **User-partitioned vector index** — chunking, local embeddings, ACL boundary before search `M`
- [x] **Source-grounded assistant** — retrieval, prompt, app-validated citations `M`
- [ ] **Manual end-to-end check** with real Tesseract (`pol`) and LM Studio on the target machine `S`

## Phase 3: Measurements & Dashboard — done
Plan: `2026-10-13-pomiary-i-pulpit.md`

- [x] **Private measurements** — validation, history, edit `S`
- [x] **History, SVG trend chart, patient dashboard** `M`
- [x] **Measurements as assistant sources** — cited measurement entries `S`

## Phase 4: Backup & LAN — **current focus**
Plan: `2026-10-13-backup-i-lan.md`

- [ ] **Encrypted backup format & roundtrip** — AES-256-GCM, scrypt KDF, versioned header with salt/nonce; DB + original files in one archive `M`
- [ ] **Safe restore & backup UI** — integrity verification before replacement, wrong password/corruption detected without overwriting, admin warning about cross-profile access `M`
- [ ] **TLS & LAN-only binding** — HTTPS for LAN clients, trusted-network bind, LM Studio stays loopback-only `M`
- [ ] **Release checks & restore instructions** — final acceptance criteria verification, documented recovery procedure `S`

Note: `tests/backup/` already exists (untracked) and imports `medical_archive.backup`, which is not yet implemented.

## Technical Debt / Hardening
- [ ] **Dependency reproducibility** — add a lock file and upper bounds for unpinned runtime deps `S`
- [ ] **Stricter linting & typing** — extend Ruff rules (I, B, UP, S, SIM), consider mypy/pyright on `src/` `S`
- [x] **CI** — run pytest and `ruff check` / `ruff format --check` automatically `S`
- [ ] **Streamed uploads** — spool uploads to disk instead of buffering up to 25 MiB in memory `S`
- [ ] **Housekeeping** — add `.idea/` to `.gitignore`, resolve duplicate `0002_` migration prefix, commit or clean up work-log references `S`

## Future Enhancements
- [ ] **Assistant quality tuning** — embedding model evaluation on Polish queries, retrieval thresholds, answer latency on Bielik 1.5B
- [ ] **Coverage reporting** for ACL-critical paths
- [ ] **ADR folder** for decisions recorded in the work-log (vector partitioning, JSON-schema response format, tokenizer approximation)

---
**Effort Scale**: `S`: 2-3 days | `M`: 1 week | `L`: 2+ weeks
