## Models

### Clear Naming
Use singular names for models and plural snake_case for tables.

### Timestamps
Every table has a tz-aware `created_at`; add `updated_at` only where the domain needs it.

### Database Constraints
Enforce data rules at the database level (NOT NULL, UNIQUE, foreign keys, CHECK).

### Appropriate Types
Choose data types that match purpose and size requirements (e.g. `Numeric(20, 8)` for measurement values).

### Index Foreign Keys
Index foreign key columns and frequently queried fields.

### Multi-Layer Validation
Validate at both model and database levels for defense in depth.

### Clear Relationships
Define relationships with appropriate cascade behaviors and naming.

### Practical Normalization
Balance normalization with query performance needs.

## Project-specific standards (discovered)

### Typed SQLAlchemy 2 models on SQLite
SQLite is the only supported backend (`database_url` must be a SQLite URL; default `<data_dir>/medical_archive.db`); every connection sets `PRAGMA foreign_keys=ON` (`check_same_thread=False`). Models subclass the shared `Base` and use `Mapped[...]` + `mapped_column()` exclusively. Table names are plural snake_case. Owner FKs are `owner_user_id` / `*_user_id` with `ondelete="CASCADE"`. Every table has a tz-aware `created_at` with `server_default=func.current_timestamp()`. User-owned resources use `String(36)` UUID4 primary keys; identity tables use Integer ids. Name constraints/indexes `ix_<table>_<cols>`, `ck_<table>_<rule>`, `uq_<table>_<rule>`; enum-like statuses are `String` + `CheckConstraint`.
```python
id: Mapped[str] = mapped_column(String(36), primary_key=True)
owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
```
