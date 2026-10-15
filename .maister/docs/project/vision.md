# Project Vision

## Pitch
**Medyczne Archiwum** is a private, LAN-only web application that helps household members organise their own medical documents, prescriptions and manual measurements, and find information in them through a local, source-grounded AI assistant — without any data leaving the home computer.

## Problem Statement
Household medical records (lab results, discharge summaries, prescriptions, scans, home measurements) end up scattered across paper, e-mail and phone photos. Finding "what was my last cholesterol result" or "when was that prescription issued" is slow, and cloud tools that could help require sending highly sensitive data to third parties.

The project solves this locally: documents are imported, OCR'd and indexed on one home computer, and a local LLM answers questions strictly from the user's own records, with clickable citations.

## Target Users
- **Household members** — each has a separate account and a private archive; they import documents, record measurements and ask the assistant questions.
- **Household administrator** — bootstraps the installation, invites members with one-time invitations, and manages encrypted full backups. Has **no** access to other members' data through the UI, API or assistant (the only accepted exception: full encrypted backup).

## Key Features
1. Independent accounts, login, one-time invitations, explicit per-record sharing and revocation.
2. Import of PDFs, photos and scans (including prescriptions) and free-text entries.
3. PDF text-layer extraction or local Polish OCR (Tesseract), with original + recognised text preview.
4. Manual confirmation of document type and date before a document enters the archive and the index.
5. Document archive with filters and a timeline.
6. Manual measurements (name, value, unit, timestamp, note) with history table and trend chart.
7. Assistant (Bielik 1.5B v3.0 Instruct via LM Studio) answering about documents and measurements, with app-validated citations.
8. Manual encrypted backup export and restore (AES-256-GCM + scrypt).

**Out of scope:** diagnosis, treatment recommendations, medication reminders/dosing, integrations with clinics/pharmacies, cloud sync, automatic device readings, public Internet access, unit conversion.

## Success Criteria
Taken from the accepted design spec acceptance criteria:
1. An account without a grant cannot read or search another person's documents or measurements via UI, API or RAG.
2. Unconfirmed imports never appear in the archive or search results.
3. Citations open the correct document and page, or the referenced measurement.
4. With missing sources, poor OCR or unavailable LM Studio the app reports the limitation and never fabricates an answer.
5. Measurements can be added, corrected, listed chronologically and charted, and cited by the assistant.
6. An encrypted backup restores to a consistent state; a wrong password or corruption is detected without overwriting existing data.
7. No data is sent to external services; the model service is not reachable from other LAN devices.

## Differentiators
- **Fully local** — no telemetry, no external APIs, no CDN assets; documents, chunks, embeddings and questions never leave the host.
- **Privacy by construction** — access filtering happens *before* vector search (user-partitioned `sqlite-vec` index), not by post-filtering results.
- **Honest assistant** — answers only from retrieved sources, refuses when sources are insufficient, treats document content as untrusted input.
- **Polish-first UX** — Polish UI, Polish OCR, Polish-language LLM (Bielik).

## Current Focus
Finish **plan 4 (backup & LAN)**: encrypted backup/restore, HTTPS/TLS with LAN-only binding, and final release checks. The README explicitly states the app is not ready for real medical data until HTTPS and a verified backup/restore exist.

---
*Source documents*: `docs/superpowers/specs/2026-10-13-medyczne-archiwum-design.md`, `docs/superpowers/plans/`, `README.md`
