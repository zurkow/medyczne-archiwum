## Domain Rules

### Owner-approved document lifecycle
A document always stays with the importing account; OCR type/date suggestions are non-authoritative and never change `owner_user_id`. States: `needs_review -> indexing -> ready`, plus `ocr_failed` / `index_failed`. Only `ready` documents appear in the archive, are indexed or can be shared; only the owner can preview, edit metadata, approve or retry. Originals survive processing failures.

### Decimal measurements in UTC
Accept and validate measurement values as `Decimal` and store as `Numeric(20, 8)` — never convert through `float`. Reject NaN, infinities, missing timezone, empty name/unit and overlong fields; normalise time to UTC. Lists require time bounds/pagination and a stable sort (time, id). Re-validate all user-submitted metadata server-side. No automatic unit conversion.
