## Configuration

### Validated settings, no default secrets
All runtime configuration lives on one pydantic-settings `Settings` class with `env_prefix="MEDICAL_ARCHIVE_"`, `extra="ignore"`, validated at startup. Numeric settings declare bounds with `Field(gt/ge/le)`; string/path settings use `@field_validator`/`@model_validator` raising `ValueError` with an actionable message. No secrets or implied default keys in the repository: `MEDICAL_ARCHIVE_SESSION_SECRET` (≥32 bytes) is mandatory outside `Settings(testing=True)`. The first admin is created only via `python -m medical_archive.auth.bootstrap`.
```python
retrieval_top_k: int = Field(default=8, gt=0, le=100)
```

### Fail-closed production profile
In the production profile, a missing database, sqlite-vec, valid session secret or TLS certificate/key stops startup. The bind address must be an explicit private LAN IP — not `0.0.0.0`, loopback, multicast or a public IP.
Status: planned in plan 4 (`docs/superpowers/plans/2026-10-13-backup-i-lan.md`), not yet implemented.
