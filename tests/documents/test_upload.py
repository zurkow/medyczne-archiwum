from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from medical_archive.models import User

_PASSWORD = "correct horse battery"
_PDF_CONTENT = b"%PDF-1.4 test document"


@pytest.fixture
def document_owner(auth_db: Session) -> User:
    user = User(
        email="document-owner@example.test",
        password_hash=PasswordHasher().hash(_PASSWORD),
        is_admin=False,
    )
    auth_db.add(user)
    auth_db.commit()
    return user


@pytest.fixture
def owner_client(
    app,
    client: TestClient,
    document_owner: User,
    extract_csrf_token,
    tmp_path: Path,
) -> TestClient:
    app.state.data_dir = tmp_path
    login_page = client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": document_owner.email,
            "password": _PASSWORD,
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    csrf_token = extract_csrf_token(client.get("/").text)
    client.headers.update({"X-CSRF-Token": csrf_token})
    return client


def test_upload_accepts_allowed_formats_and_keeps_pdf_private_for_review(
    owner_client: TestClient,
    auth_db: Session,
    app,
):
    response = owner_client.post(
        "/documents",
        files={"file": ("blood.pdf", _PDF_CONTENT, "application/pdf")},
    )

    assert response.status_code == 202
    document = response.json()
    assert document["status"] == "needs_review"
    assert "storage_key" not in document

    stored_key = auth_db.execute(
        text("SELECT storage_key FROM documents WHERE id = :document_id"),
        {"document_id": document["id"]},
    ).scalar_one()
    stored_path = app.state.data_dir / "documents" / stored_key
    assert stored_path.is_file()
    assert stored_path.read_bytes() == _PDF_CONTENT
    assert stored_path.name != "blood.pdf"

    for filename, content, mime_type in (
        ("scan.jpg", b"\xff\xd8\xff\xdbjpeg", "image/jpeg"),
        ("scan.png", b"\x89PNG\r\n\x1a\npng", "image/png"),
    ):
        image_response = owner_client.post(
            "/documents",
            files={"file": (filename, content, mime_type)},
        )
        assert image_response.status_code == 202
        assert image_response.json()["mime_type"] == mime_type


def test_upload_rejects_executable_disguised_as_pdf(owner_client: TestClient):
    response = owner_client.post(
        "/documents",
        files={"file": ("scan.pdf", b"MZ executable", "application/pdf")},
    )

    assert response.status_code == 415


def test_upload_rejects_extension_that_does_not_match_file_signature(
    owner_client: TestClient,
):
    response = owner_client.post(
        "/documents",
        files={"file": ("scan.png", _PDF_CONTENT, "application/pdf")},
    )

    assert response.status_code == 415


def test_upload_rejects_file_over_streaming_size_limit(owner_client: TestClient):
    response = owner_client.post(
        "/documents",
        files={"file": ("large.pdf", b"%PDF-1.4" + b"x" * (25 * 1024 * 1024), "application/pdf")},
    )

    assert response.status_code == 413


def test_database_failure_removes_only_the_newly_written_file(
    owner_client: TestClient,
    app,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_commit(_session: Session) -> None:
        raise SQLAlchemyError("database internals must not be returned")

    with monkeypatch.context() as failing_database:
        failing_database.setattr(Session, "commit", fail_commit)
        response = owner_client.post(
            "/documents",
            files={"file": ("blood.pdf", _PDF_CONTENT, "application/pdf")},
        )

        assert response.status_code == 503
        assert "database internals" not in response.text
        assert list((app.state.data_dir / "documents").iterdir()) == []

    def fail_atomic_write(_temporary_path: Path, _final_path: Path) -> None:
        raise OSError("filesystem internals must not be returned")

    with monkeypatch.context() as failing_storage:
        failing_storage.setattr("medical_archive.documents.storage.os.replace", fail_atomic_write)
        response = owner_client.post(
            "/documents",
            files={"file": ("blood.pdf", _PDF_CONTENT, "application/pdf")},
        )

        assert response.status_code == 500
        assert "filesystem internals" not in response.text
        assert list((app.state.data_dir / "documents").iterdir()) == []
