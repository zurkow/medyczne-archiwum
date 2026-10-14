import re
import sqlite3
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medical_archive.backup import crypto
from medical_archive.backup import format as archive_format
from medical_archive.backup.service import backup_operation_lock
from medical_archive.documents.models import DocumentPage
from medical_archive.models import AccessGrant, Document, Measurement
from medical_archive.rag.index import load_sqlite_vec
from medical_archive.rag.models import ChunkMetadata

ADMIN_PASSWORD = "correct horse battery"
BACKUP_PASSWORD = "synthetic backup passphrase"
EXPORT_SUMMARY = "Nie utworzono kopii. Popraw zaznaczone pola."
RESTORE_SUMMARY = "Nie odtworzono kopii. Obecne dane nie zostały zmienione."
RESTORE_INVALID_BACKUP = (
    "Nie można odtworzyć kopii: plik jest uszkodzony albo hasło jest nieprawidłowe. "
    "Obecne dane nie zostały zmienione."
)
UPLOAD_FILENAME = "synthetic-upload-name.marchive"
_FILENAME_PATTERN = re.compile(r'^attachment; filename="medyczne-archiwum-\d{8}-\d{6}\.marchive"$')
_ALERT_IDS = {"export": "export-error", "restore": "restore-error"}
_SUMMARIES = {"export": EXPORT_SUMMARY, "restore": RESTORE_SUMMARY}
_PASSWORD_FIELDS = ("admin_password", "backup_password", "backup_password_repeat")
_COMPARED_TABLES = (
    "users",
    "access_grants",
    "documents",
    "document_pages",
    "measurements",
    "rag_chunk_metadata",
)


def _form_fields(
    client: TestClient, extract_csrf_token, form: str, **overrides: str | None
) -> dict:
    page = client.get("/settings/backup")
    assert page.status_code == 200
    fields = {
        "csrf_token": extract_csrf_token(page.text),
        "admin_password": ADMIN_PASSWORD,
        "backup_password": BACKUP_PASSWORD,
    }
    if form == "export":
        fields["backup_password_repeat"] = BACKUP_PASSWORD
    fields["confirm"] = "yes"
    fields.update(overrides)
    return {name: value for name, value in fields.items() if value is not None}


def _export_fields(client: TestClient, extract_csrf_token, **overrides: str | None) -> dict:
    return _form_fields(client, extract_csrf_token, "export", **overrides)


def _post_export(client: TestClient, fields: dict):
    return client.post("/settings/backup/export", data=fields, follow_redirects=False)


def _post_restore(client: TestClient, fields: dict, content: bytes = b"synthetic envelope"):
    return client.post(
        "/settings/backup/restore",
        data=fields,
        files={"backup_file": (UPLOAD_FILENAME, content, "application/octet-stream")},
        follow_redirects=False,
    )


def _submit(client: TestClient, form: str, fields: dict, content: bytes = b"synthetic envelope"):
    if form == "export":
        return _post_export(client, fields)
    return _post_restore(client, fields, content)


def _temp_entries(data_dir: Path) -> list[Path]:
    entries: list[Path] = []
    for name in (".backup-tmp", ".restore-staging"):
        temp_dir = data_dir / name
        if temp_dir.exists():
            entries.extend(temp_dir.rglob("*"))
    return entries


def _input_tag(document: str, element_id: str) -> str:
    match = re.search(rf'<input[^>]*\bid="{re.escape(element_id)}"[^>]*>', document)
    assert match is not None, element_id
    return match.group(0)


def _alert_text(document: str, alert_id: str) -> str:
    match = re.search(rf'<p role="alert" id="{alert_id}"[^>]*>\s*([^<]*?)\s*</p>', document)
    assert match is not None, alert_id
    return match.group(1)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    load_sqlite_vec(connection)
    return connection


def _live_state(database_path: Path) -> dict[str, list[tuple]]:
    connection = _connect(database_path)
    try:
        state = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in _COMPARED_TABLES
        }
        state["rag_chunk_vectors"] = connection.execute(
            "SELECT chunk_id, owner_partition, vec_to_json(embedding) "
            "FROM rag_chunk_vectors ORDER BY chunk_id"
        ).fetchall()
        return state
    finally:
        connection.close()


def _documents_listing(documents_dir: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(documents_dir.iterdir())}


def _seed_archive(auth_session_factory, documents_dir: Path, *, owner, grantee) -> str:
    storage_key = str(uuid.uuid4())
    original = b"%PDF-1.4 synthetic roundtrip original"
    document_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    with auth_session_factory() as session:
        session.add(
            Document(
                id=document_id,
                owner_user_id=owner.id,
                storage_key=storage_key,
                original_filename="synthetic-result.pdf",
                mime_type="application/pdf",
                byte_size=len(original),
                processing_status="ready",
            )
        )
        session.flush()
        session.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                text="Synthetic page text",
                extraction_method="text",
            )
        )
        session.add(
            AccessGrant(
                owner_user_id=owner.id,
                grantee_user_id=grantee.id,
                resource_type="document",
                resource_id=document_id,
            )
        )
        session.add(
            Measurement(
                id=str(uuid.uuid4()),
                owner_user_id=owner.id,
                name="Synthetic glucose",
                value=Decimal("5.40000000"),
                unit="mmol/L",
                measured_at_utc=datetime(2026, 1, 2, 8, 30, tzinfo=UTC),
            )
        )
        session.add(
            ChunkMetadata(
                chunk_id=chunk_id,
                owner_partition=str(owner.id),
                document_id=document_id,
                page_number=1,
            )
        )
        session.commit()
        connection = session.connection().connection.driver_connection
        load_sqlite_vec(connection)
        connection.execute(
            "INSERT INTO rag_chunk_vectors (chunk_id, owner_partition, embedding) VALUES (?, ?, ?)",
            (chunk_id, str(owner.id), str([0.5, *([0.0] * 767)])),
        )
        connection.commit()
    (documents_dir / storage_key).write_bytes(original)
    return storage_key


def _mutate_live_store(database_path: Path, documents_dir: Path, storage_key: str) -> None:
    connection = _connect(database_path)
    try:
        connection.execute("DELETE FROM rag_chunk_vectors")
        connection.execute("DELETE FROM rag_chunk_metadata")
        connection.execute("DELETE FROM access_grants")
        connection.execute("DELETE FROM measurements")
        connection.execute("UPDATE document_pages SET text = 'Mutated synthetic text'")
        connection.commit()
    finally:
        connection.close()
    (documents_dir / storage_key).write_bytes(b"%PDF-1.4 mutated synthetic original")
    (documents_dir / str(uuid.uuid4())).write_bytes(b"synthetic stray original")


def _export_envelope(client: TestClient, extract_csrf_token) -> bytes:
    response = _post_export(client, _export_fields(client, extract_csrf_token))
    assert response.status_code == 200
    return response.content


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/settings/backup"),
        ("post", "/settings/backup/export"),
        ("post", "/settings/backup/restore"),
    ],
)
def test_member_gets_403_and_anonymous_is_redirected_from_backup_routes(
    backup_app, member_client, backup_data_dir, method, path
):
    member_response = getattr(member_client, method)(path, follow_redirects=False)

    assert member_response.status_code == 403
    assert "Administrator access is required." in member_response.text
    with TestClient(backup_app, base_url="https://testserver") as anonymous:
        anonymous_response = getattr(anonymous, method)(path, follow_redirects=False)
    assert anonymous_response.status_code == 303
    assert anonymous_response.headers["location"] == "/auth/login"
    assert _temp_entries(backup_data_dir) == []


@pytest.mark.parametrize(
    ("form", "csrf_token"),
    [
        ("export", None),
        ("export", "forged-token"),
        ("restore", None),
        ("restore", "forged-token"),
    ],
    ids=["export-missing", "export-forged", "restore-missing", "restore-forged"],
)
def test_backup_post_without_csrf_is_forbidden_and_leaves_no_temp_files(
    admin_client, extract_csrf_token, backup_data_dir, form, csrf_token
):
    fields = _form_fields(admin_client, extract_csrf_token, form, csrf_token=csrf_token)

    response = _submit(admin_client, form, fields)

    assert response.status_code == 403
    assert "Invalid or missing CSRF token." in response.text
    assert "content-disposition" not in response.headers
    assert _temp_entries(backup_data_dir) == []


@pytest.mark.parametrize(
    ("form", "overrides", "content", "status_code", "field_id", "message"),
    [
        (
            "export",
            {"admin_password": "wrong synthetic admin secret"},
            None,
            403,
            "export-admin-password",
            "Hasło administratora jest nieprawidłowe.",
        ),
        (
            "export",
            {"confirm": None},
            None,
            400,
            "export-confirm",
            "Zaznacz potwierdzenie, aby utworzyć kopię.",
        ),
        (
            "export",
            {"backup_password_repeat": "different synthetic passphrase"},
            None,
            400,
            "export-backup-password-repeat",
            "Hasła kopii nie są takie same.",
        ),
        (
            "export",
            {"backup_password": "short-pass1", "backup_password_repeat": "short-pass1"},
            None,
            400,
            "export-backup-password",
            "Hasło kopii musi mieć od 12 do 1024 znaków.",
        ),
        (
            "restore",
            {"admin_password": "wrong synthetic admin secret"},
            b"synthetic envelope",
            403,
            "restore-admin-password",
            "Hasło administratora jest nieprawidłowe.",
        ),
        (
            "restore",
            {"confirm": None},
            b"synthetic envelope",
            400,
            "restore-confirm",
            "Zaznacz potwierdzenie, aby odtworzyć kopię.",
        ),
        (
            "restore",
            {},
            b"",
            400,
            "restore-file",
            "Wybierz plik kopii (.marchive).",
        ),
    ],
    ids=[
        "export-wrong-admin-password",
        "export-missing-confirmation",
        "export-repeat-mismatch",
        "export-short-password",
        "restore-wrong-admin-password",
        "restore-missing-confirmation",
        "restore-empty-file",
    ],
)
def test_wrong_admin_password_or_missing_confirmation_is_rejected_without_changes(
    admin_client,
    extract_csrf_token,
    backup_data_dir,
    form,
    overrides,
    content,
    status_code,
    field_id,
    message,
):
    fields = _form_fields(admin_client, extract_csrf_token, form, **overrides)

    response = _submit(admin_client, form, fields, content)

    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.count('role="alert"') == 1
    assert _alert_text(response.text, _ALERT_IDS[form]) == _SUMMARIES[form]
    assert response.text.count('aria-invalid="true"') == 1
    field_tag = _input_tag(response.text, field_id)
    assert 'aria-invalid="true"' in field_tag
    assert f"{field_id}-error" in field_tag
    assert re.search(rf'id="{field_id}-error"[^>]*>\s*✖ {re.escape(message)}', response.text)
    for name in _PASSWORD_FIELDS:
        if name in fields:
            assert fields[name] not in response.text
    assert UPLOAD_FILENAME not in response.text
    assert "checked" not in _input_tag(response.text, f"{form}-confirm")
    assert _temp_entries(backup_data_dir) == []


@pytest.mark.parametrize("form", ["export", "restore"])
def test_concurrent_backup_operation_returns_409(
    admin_client, extract_csrf_token, backup_data_dir, form
):
    fields = _form_fields(admin_client, extract_csrf_token, form)

    with backup_operation_lock():
        response = _submit(admin_client, form, fields)

    assert response.status_code == 409
    assert response.text.count('role="alert"') == 1
    assert _alert_text(response.text, _ALERT_IDS[form]) == "Operacja kopii zapasowej już trwa."
    assert "content-disposition" not in response.headers
    assert _temp_entries(backup_data_dir) == []


def test_export_downloads_no_store_attachment_and_removes_server_copy(
    admin_client, extract_csrf_token, backup_data_dir
):
    fields = _export_fields(admin_client, extract_csrf_token)

    response = _post_export(admin_client, fields)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert _FILENAME_PATTERN.match(response.headers["content-disposition"])
    assert response.headers["cache-control"] == "no-store"
    manifest = archive_format.validate_archive(crypto.decrypt(response.content, BACKUP_PASSWORD))
    assert manifest["storage_keys"] == []
    assert not [entry for entry in _temp_entries(backup_data_dir) if entry.suffix == ".marchive"]
    assert [entry for entry in _temp_entries(backup_data_dir) if entry.is_file()] == []


def test_restore_with_corrupt_or_wrong_password_backup_returns_identical_422_and_keeps_live_data(
    admin_client,
    extract_csrf_token,
    auth_engine,
    auth_session_factory,
    backup_data_dir,
    admin_user,
    member_user,
):
    documents_dir = backup_data_dir / "documents"
    _seed_archive(auth_session_factory, documents_dir, owner=member_user, grantee=admin_user)
    envelope = _export_envelope(admin_client, extract_csrf_token)
    database_path = Path(auth_engine.url.database)
    state_before = _live_state(database_path)
    documents_before = _documents_listing(documents_dir)

    corrupt = _post_restore(
        admin_client,
        _form_fields(admin_client, extract_csrf_token, "restore"),
        b"synthetic corrupt envelope bytes" * 8,
    )
    wrong_password = _post_restore(
        admin_client,
        _form_fields(
            admin_client,
            extract_csrf_token,
            "restore",
            backup_password="wrong synthetic backup passphrase",
        ),
        envelope,
    )

    for response in (corrupt, wrong_password):
        assert response.status_code == 422
        assert response.text.count('role="alert"') == 1
        assert 'aria-invalid="true"' not in response.text
        assert UPLOAD_FILENAME not in response.text
    assert _alert_text(corrupt.text, "restore-error") == RESTORE_INVALID_BACKUP
    assert _alert_text(wrong_password.text, "restore-error") == RESTORE_INVALID_BACKUP
    assert "wrong synthetic backup passphrase" not in wrong_password.text
    assert _live_state(database_path) == state_before
    assert _documents_listing(documents_dir) == documents_before
    assert _temp_entries(backup_data_dir) == []


@pytest.mark.parametrize("backup_max_bytes", [1024 * 1024])
def test_restore_over_size_cap_returns_413(admin_client, extract_csrf_token, backup_data_dir):
    fields = _form_fields(admin_client, extract_csrf_token, "restore")

    response = _post_restore(admin_client, fields, b"\0" * (1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.text.count('role="alert"') == 1
    assert _alert_text(response.text, "restore-error") == (
        "Plik kopii jest za duży (maksymalnie 1 MiB). Obecne dane nie zostały zmienione."
    )
    file_tag = _input_tag(response.text, "restore-file")
    assert 'aria-invalid="true"' in file_tag
    assert "restore-file-error" in file_tag
    assert _temp_entries(backup_data_dir) == []


def test_full_backup_restore_roundtrip_returns_accounts_grants_documents_vectors_and_measurements(
    backup_app,
    admin_client,
    extract_csrf_token,
    auth_engine,
    auth_session_factory,
    backup_data_dir,
    admin_user,
    member_user,
):
    documents_dir = backup_data_dir / "documents"
    storage_key = _seed_archive(
        auth_session_factory, documents_dir, owner=member_user, grantee=admin_user
    )
    database_path = Path(auth_engine.url.database)
    state_before = _live_state(database_path)
    documents_before = _documents_listing(documents_dir)
    envelope = _export_envelope(admin_client, extract_csrf_token)
    _mutate_live_store(database_path, documents_dir, storage_key)
    assert _live_state(database_path) != state_before
    old_cookie = admin_client.cookies.get("session")

    response = _post_restore(
        admin_client, _form_fields(admin_client, extract_csrf_token, "restore"), envelope
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?restored=1"
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("session=")
    assert "Max-Age=0" in set_cookie
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=strict" in set_cookie
    assert admin_client.cookies.get("session") is None
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)
    finally:
        connection.close()
    assert backup_app.state.maintenance.active is False
    with TestClient(
        backup_app, base_url="https://testserver", cookies={"session": old_cookie}
    ) as stale:
        stale_response = stale.get("/", follow_redirects=False)
    assert stale_response.status_code == 303
    assert stale_response.headers["location"] == "/auth/login"
    login_page = admin_client.get("/auth/login?restored=1")
    assert "Kopia została odtworzona. Zaloguj się ponownie." in login_page.text
    assert _live_state(database_path) == state_before
    assert _documents_listing(documents_dir) == documents_before
    assert _temp_entries(backup_data_dir) == []


def test_maintenance_mode_returns_503_for_everything_except_health(backup_app):
    backup_app.state.maintenance.enter()
    try:
        with TestClient(backup_app, base_url="https://testserver") as test_client:
            blocked = [
                test_client.get(path, follow_redirects=False)
                for path in ("/", "/static/app.css", "/auth/login")
            ]
            health = test_client.get("/health")
    finally:
        backup_app.state.maintenance.exit()

    for response in blocked:
        assert response.status_code == 503
        assert response.headers["retry-after"] == "30"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith("text/html")
        assert "<h1>Trwa odtwarzanie kopii zapasowej…</h1>" in response.text
        assert "/static/" not in response.text
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_login_notice_ignores_unexpected_restored_values(client):
    restored = client.get("/auth/login", params={"restored": "1"})

    assert restored.status_code == 200
    assert 'role="status"' in restored.text
    assert "Kopia została odtworzona. Zaloguj się ponownie." in restored.text
    assert "Użyj hasła, które obowiązywało w chwili tworzenia kopii." in restored.text

    injected = client.get("/auth/login", params={"restored": "<script>alert(1)</script>"})

    assert injected.status_code == 200
    assert "Kopia została odtworzona." not in injected.text
    assert 'role="status"' not in injected.text
    assert "<script>" not in injected.text
    assert "alert(1)" not in injected.text
