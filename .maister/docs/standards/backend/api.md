## API Design

### RESTful Principles
Use resource-based URLs with appropriate HTTP methods; HTML forms use POST actions (see "JSON endpoints plus `/ui` HTML pages").

### Consistent Naming
Use lowercase, underscored or hyphenated names consistently across endpoints.

### No URL Versioning
Routes are mounted at `/<domain>` without a version segment; the API serves only this app's own UI and is not a public contract.

### Plural Nouns
Use plural nouns for resources (`/documents`, `/measurements`).

### Limited Nesting
Keep URL nesting to 2-3 levels maximum for readability.

### Query Parameters
Use query parameters for filtering, sorting, and pagination.

### Proper Status Codes
Return appropriate HTTP status codes (200, 201, 202, 204, 303, 400, 403, 404, 413, 415); resources hidden from the caller return 404, never 403 (see `security/access-control.md`).

## Project-specific standards (discovered)

### Annotated Depends and dependency factories
Declare FastAPI dependencies as `Annotated[T, Depends(fn)]`, never `= Depends()`. Standard dependencies: `get_session`, `get_current_user`, `get_authenticated_session`, `require_admin`. Parametrised dependencies and routers come from factory functions (`require_resource_access(...)`, `create_grant_router(...)`). Swappable collaborators (indexer, extractor, answer service) are read from `request.app.state` with a production fallback.
```python
user: Annotated[User, Depends(get_current_user)],
session: Annotated[Session, Depends(get_session)],
```

### JSON endpoints plus `/ui` HTML pages
Each domain exposes JSON endpoints at the collection root (`""`, `"/{resource_id}"`) and server-rendered pages under `"/ui"` plus POST form actions (`/{resource_id}/edit`, `/{resource_id}/delete`). HTML routes use `response_class=HTMLResponse, include_in_schema=False` and render via `render_template(request, name, title=..., **context)`. Successful form POSTs redirect with `RedirectResponse(..., status_code=303)`. The path parameter is always `resource_id`. JSON create returns 201 (202 for async upload), delete returns 204. OpenAPI/docs stay disabled.

### Explicit `public_*()` serializers
Never return ORM entities directly. Resource services provide `public_<entity>()` functions that build an explicit allow-listed dict, with timestamps as UTC ISO strings ending in `Z`.
```python
def public_measurement(measurement: Measurement) -> dict[str, str | float | None]:
```
