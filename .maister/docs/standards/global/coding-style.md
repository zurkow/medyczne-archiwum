## Coding Style

### Naming Consistency
Follow established naming patterns for variables, functions, classes, and files throughout the project.

### Automatic Formatting
Use automated tools to enforce consistent indentation, spacing, and line breaks (Ruff format, line length 100).

### Descriptive Names
Choose names that clearly communicate intent; avoid cryptic abbreviations or single-letter identifiers outside tight loops.

### Focused Functions
Write functions that do one thing well; smaller functions are easier to read, test, and maintain.

### Uniform Indentation
Standardize on spaces or tabs and enforce with editor/linter settings.

### No Dead Code
Remove unused imports, commented-out blocks, and orphaned functions instead of leaving them behind.

### No Backward Compatibility Unless Required
Avoid extra code paths for backward compatibility unless explicitly needed.

### DRY (Don't Repeat Yourself)
Extract repeated logic into reusable functions or modules.

## Project-specific standards (discovered)

### Absolute, grouped imports
All intra-project imports are absolute from `medical_archive` — no relative imports. Group stdlib / third-party / first-party separated by blank lines, sorted alphabetically; wrap multi-name imports in parentheses, one name per line. `from __future__ import annotations` is not a project convention.
```python
from medical_archive.auth.service import (
    AuthorizationError,
    create_invitation,
)
```

### Type hints and keyword-only arguments
Use full type hints with modern syntax (`X | None`, built-in generics, `collections.abc`) and declare return types. Functions with more than ~2 parameters put options after `*` (keyword-only), with the `Session`/primary object positional first. Time-dependent functions accept `now: datetime | None = None` and use timezone-aware `datetime.now(UTC)`. Prefix module-private helpers and constants with `_`.
```python
def find_valid_session(
    database: Session,
    raw_token: str | None,
    *,
    allow_preauth: bool = False,
    now: datetime | None = None,
) -> LoginSession | None:
```

### Frozen dataclasses for value objects
Non-ORM value/result objects (extracted pages, suggestions, retrieved sources, answer results, trend points) are `@dataclass(frozen=True)`. Pydantic `BaseModel` with `ConfigDict(extra="forbid")` is used only for request validation.
