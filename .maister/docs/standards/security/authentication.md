## Authentication & Request Integrity

### Argon2id, hashed opaque tokens, strict cookies
Hash passwords with Argon2id (`argon2-cffi`). Session and invitation tokens come from `secrets.token_urlsafe(32)` and only `SHA256(token)` is stored; invitations are single-use and time-limited. The session cookie is `HttpOnly`, `Secure`, `SameSite=Strict`, path `/`, max 12 h. Failed login returns the same message for an unknown account and a wrong password.

### CSRF on every state-changing request
Every mutating endpoint verifies a synchronizer CSRF token bound to the login session (stored hashed, compared with `hmac.compare_digest`). HTML forms send a hidden `csrf_token` input; fetch/JSON requests send `X-CSRF-Token`. Failure returns `403 "Invalid or missing CSRF token."`. GET pages rotate the token via `rotate_csrf()`. Login/registration use short-lived, purpose-scoped pre-auth sessions that never authorize protected routes; rotate session and CSRF on login.

### Strict manual form parsing
URL-encoded form bodies are parsed with a byte cap (`_MAX_FORM_BYTES` → 413), a required content type (415 otherwise), `parse_qs` with `max_num_fields`, and rejection of duplicate fields (400).
