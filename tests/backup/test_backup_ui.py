import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medical_archive.backup import crypto
from medical_archive.backup import format as archive_format
from medical_archive.backup.service import backup_operation_lock

ADMIN_PASSWORD = "correct horse battery"
BACKUP_PASSWORD = "synthetic backup passphrase"
EXPORT_SUMMARY = "Nie utworzono kopii. Popraw zaznaczone pola."
_FILENAME_PATTERN = re.compile(r'^attachment; filename="medyczne-archiwum-\d{8}-\d{6}\.marchive"$')


def _export_fields(client: TestClient, extract_csrf_token, **overrides: str | None) -> dict:
    page = client.get("/settings/backup")
    assert page.status_code == 200
    fields = {
        "csrf_token": extract_csrf_token(page.text),
        "admin_password": ADMIN_PASSWORD,
        "backup_password": BACKUP_PASSWORD,
        "backup_password_repeat": BACKUP_PASSWORD,
        "confirm": "yes",
    }
    fields.update(overrides)
    return {name: value for name, value in fields.items() if value is not None}


def _post_export(client: TestClient, fields: dict):
    return client.post("/settings/backup/export", data=fields, follow_redirects=False)


def _temp_entries(data_dir: Path) -> list[Path]:
    temp_dir = data_dir / ".backup-tmp"
    return list(temp_dir.rglob("*")) if temp_dir.exists() else []


def _input_tag(document: str, element_id: str) -> str:
    match = re.search(rf'<input[^>]*\bid="{re.escape(element_id)}"[^>]*>', document)
    assert match is not None, element_id
    return match.group(0)


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/settings/backup"), ("post", "/settings/backup/export")],
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


@pytest.mark.parametrize("csrf_token", [None, "forged-token"], ids=["missing", "forged"])
def test_backup_post_without_csrf_is_forbidden_and_leaves_no_temp_files(
    admin_client, extract_csrf_token, backup_data_dir, csrf_token
):
    fields = _export_fields(admin_client, extract_csrf_token, csrf_token=csrf_token)

    response = _post_export(admin_client, fields)

    assert response.status_code == 403
    assert "Invalid or missing CSRF token." in response.text
    assert "content-disposition" not in response.headers
    assert _temp_entries(backup_data_dir) == []


@pytest.mark.parametrize(
    ("overrides", "status_code", "field_id", "message"),
    [
        (
            {"admin_password": "wrong synthetic admin secret"},
            403,
            "export-admin-password",
            "Hasło administratora jest nieprawidłowe.",
        ),
        ({"confirm": None}, 400, "export-confirm", "Zaznacz potwierdzenie, aby utworzyć kopię."),
        (
            {"backup_password_repeat": "different synthetic passphrase"},
            400,
            "export-backup-password-repeat",
            "Hasła kopii nie są takie same.",
        ),
        (
            {"backup_password": "short-pass1", "backup_password_repeat": "short-pass1"},
            400,
            "export-backup-password",
            "Hasło kopii musi mieć od 12 do 1024 znaków.",
        ),
    ],
    ids=["wrong-admin-password", "missing-confirmation", "repeat-mismatch", "short-password"],
)
def test_wrong_admin_password_or_missing_confirmation_is_rejected_without_changes(
    admin_client, extract_csrf_token, backup_data_dir, overrides, status_code, field_id, message
):
    fields = _export_fields(admin_client, extract_csrf_token, **overrides)

    response = _post_export(admin_client, fields)

    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.count('role="alert"') == 1
    assert re.search(rf'<p role="alert" id="export-error"[^>]*>\s*{EXPORT_SUMMARY}', response.text)
    assert response.text.count('aria-invalid="true"') == 1
    field_tag = _input_tag(response.text, field_id)
    assert 'aria-invalid="true"' in field_tag
    assert f"{field_id}-error" in field_tag
    assert re.search(rf'id="{field_id}-error"[^>]*>\s*✖ {re.escape(message)}', response.text)
    for name in ("admin_password", "backup_password", "backup_password_repeat"):
        assert fields[name] not in response.text
    assert "checked" not in _input_tag(response.text, "export-confirm")
    assert _temp_entries(backup_data_dir) == []


def test_concurrent_backup_operation_returns_409(admin_client, extract_csrf_token, backup_data_dir):
    fields = _export_fields(admin_client, extract_csrf_token)

    with backup_operation_lock():
        response = _post_export(admin_client, fields)

    assert response.status_code == 409
    assert response.text.count('role="alert"') == 1
    assert "Operacja kopii zapasowej już trwa." in response.text
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
