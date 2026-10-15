## Data Protection

### No sensitive data in logs
Use a module-level `logger = logging.getLogger(__name__)` and event-style messages `<domain>_<event>_<outcome> key=%s ...` containing only opaque IDs, error codes/types, `duration_ms` and request ID. Never log document text, OCR output, filenames, questions/prompts, measurement values, vectors, passwords or backup passwords.
```python
logger.warning("document_extraction_failed document_id=%s code=%s duration_ms=%d", document.id, error.code, elapsed_ms)
```

### Private file storage, streamed uploads
Store originals outside static/webroot under `<data_dir>/documents` with a random UUID storage key — never a path built from the client filename — written atomically. Serve them only through a permission-checking endpoint. Validate upload size while streaming to disk (not by buffering the whole body in memory) and check the real file signature plus extension.
Note: current upload code still buffers up to 25 MiB in memory — tech debt to fix.

### Local-only data flow
No telemetry, external APIs or CDN assets; document content, chunks, embeddings and questions never leave the host. Default data dir is `~/.medical-archive`. LM Studio/embedding clients accept only `http(s)` on `127.0.0.1` / `localhost` / `::1` with no credentials, query or fragment; non-loopback endpoints are rejected. The app is LAN-only over HTTPS, with no wildcard CORS, OpenAPI docs disabled and restricted Host.

### Verified, non-destructive restore
Backups use a versioned format with AES-256-GCM and a scrypt-derived key (N=2**15, r=8, p=1), containing the SQLite DB and original files. Restore fully decrypts and validates (AEAD tag, manifest, SQLite integrity, no absolute or `..` paths) before touching active data, keeps a rollback snapshot and switches atomically; a bad archive or wrong password never overwrites data. Export/restore requires CSRF, password re-entry and explicit confirmation.
Status: planned in plan 4, not yet implemented.
