## Error Handling

### Clear User Messages
Show helpful, actionable messages without exposing internal details or security-sensitive information.

### Fail Fast
Validate inputs and check preconditions early; reject invalid data before it causes deeper issues.

### Typed Exceptions
Use specific exception types instead of generic ones to enable precise error handling (see "Domain exception classes, safe messages").

### Centralized Handling
Catch and process errors at appropriate boundaries (routers translate domain errors to HTTP) rather than scattering try-catch throughout.

### Explicit Degradation
When a non-critical component fails, the app may keep running with reduced functionality, but the limited state must be explicit and visible to the user — never silent (see "No silent fallbacks or fake success").

### No Automatic Retries for Generation
Do not automatically retry assistant generation; a failed model call yields an explicit error. Any retry of other transient local operations must be bounded and visible.

### Resource Cleanup
Always release resources (file handles, connections) in finally blocks or equivalent cleanup mechanisms.

## Project-specific standards (discovered)

### Domain exception classes, safe messages
Each domain service defines its own exception classes named `*Error` with a one-line docstring "Raised when ...". Services raise domain errors; routers translate them to `HTTPException`, always chaining with `raise ... from error`. External-tool exceptions become explicit, safe UI messages; HTTP errors are readable 4xx/5xx without filesystem or system details; validation errors name the specific field.
```python
except DuplicateEmailError as error:
    raise HTTPException(status_code=400, detail="An account already exists for this email.") from error
```

### No silent fallbacks or fake success
A missing or failing component (sqlite-vec, Tesseract/language pack, LM Studio, model) produces an explicit configuration error or visible limited state — never an empty result, substitute implementation, or answer that looks correct. E.g. missing OCR raises `ExtractionError`, not empty text; `tools/check_local_ai.py` exits 1 on a missing prerequisite.

### Transactional or safe-order state changes
Changes to document metadata, the vector index and access grants are transactional or safely ordered so a failure never leaves a seemingly valid, partially written document. Invitation consumption is atomic; reindexing writes the complete new chunk set, then switches atomically.
