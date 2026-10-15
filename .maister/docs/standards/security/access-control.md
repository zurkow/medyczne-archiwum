## Access Control

### Access checked in the domain service on every endpoint
Every endpoint checks identity and permissions in the domain service, independently of the UI — hiding a button is not authorization. Use `grants.visible_resource_ids` / `can_access` / `require_owner` and the `require_resource_access` / `require_resource_owner` dependency factories. Owner IDs come from trusted DB queries — never accept `owner_user_id` from the client. The admin role never bypasses checks. Only the owner shares or revokes; grantees are read-only and cannot reshare; revocation takes effect immediately.

### 404 for hidden resources
Resources the caller does not own and has not been granted return `404 "Resource not found."` — never 403 — so existence is not leaked. This applies to administrators too. Unauthenticated users are redirected to login.
```python
raise HTTPException(status_code=404, detail="Resource not found.")
```

### Administrator has no access to others' data
Admins manage invitations and backups but never see other users' documents or measurements via UI, API or the assistant. The only accepted exception is the full encrypted backup; UI and docs must warn about it before export/restore.
