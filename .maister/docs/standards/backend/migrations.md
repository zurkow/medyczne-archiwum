## Database Migrations

### Reversible
Always implement rollback (`downgrade()`) for safe migration reversals.

### Small and Focused
Keep each migration to a single logical change.

### Separate Schema and Data
Keep schema changes separate from data migrations for safer rollbacks.

### Descriptive Names
Use names that indicate what the migration does.

### Version Control
Commit migrations; never modify existing ones after they have been applied.

## Project-specific standards (discovered)

### Reversible, hand-written Alembic migrations
Migrations live in `migrations/versions/NNNN_<description>.py` with `revision` equal to the filename stem and an explicit `down_revision`. New migrations take the next unused 4-digit number (the existing duplicate `0002_` prefix is left as-is because renaming would change revision ids). Each file starts with a one-line docstring, imports `sqlalchemy as sa` and `from alembic import op`, and implements both `upgrade() -> None` and `downgrade() -> None` (drop indexes before tables). Columns mirror the ORM model including named indexes. Migrations must survive `downgrade base` → `upgrade head`.
```python
"""Create private measurements with explicit ownership."""

revision = "0005_measurements"
down_revision = "0004_vector_index"
```

### Database URL from environment
`alembic.ini` keeps `sqlalchemy.url` empty; `migrations/env.py` resolves the URL from `MEDICAL_ARCHIVE_DATABASE_URL`, then alembic.ini, then `Settings` defaults, and uses the application `create_database()` engine with `target_metadata = Base.metadata`.
