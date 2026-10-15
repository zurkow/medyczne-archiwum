from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.documents.models import Document
from medical_archive.models import User

_PASSWORD = "correct horse battery"
_PDF_CONTENT = b"%PDF-1.4 synthetic lab result"


@pytest.fixture
def form_owner(auth_db: Session) -> User:
    user = User(
        email="form-owner@example.test",
        password_hash=PasswordHasher().hash(_PASSWORD),
        is_admin=False,
    )
    auth_db.add(user)
    auth_db.commit()
    return user


@pytest.fixture
def form_client(app, client: TestClient, form_owner: User, extract_csrf_token, tmp_path: Path):
    app.state.data_dir = tmp_path
    login_page = client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": form_owner.email,
            "password": _PASSWORD,
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def _upload(client: TestClient, extract_csrf_token, file, *, csrf_token: str | None = None):
    page = client.get("/documents/ui")
    token = extract_csrf_token(page.text) if csrf_token is None else csrf_token
    return client.post(
        "/documents/ui",
        data={"csrf_token": token},
        files={"file": file},
        follow_redirects=False,
    )


def _documents(auth_db: Session) -> list[Document]:
    auth_db.expire_all()
    return list(auth_db.scalars(select(Document)))


def _leftover_uploads(data_dir: Path) -> list[Path]:
    upload_dir = data_dir / ".upload-tmp"
    return list(upload_dir.iterdir()) if upload_dir.exists() else []


def test_documents_page_offers_a_multipart_upload_form(form_client: TestClient):
    response = form_client.get("/documents/ui")

    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="file"' in response.text
    assert "Prześlij dokument" in response.text


def test_upload_form_stores_document_for_review_and_redirects_to_it(
    form_client: TestClient, extract_csrf_token, auth_db: Session, form_owner: User, tmp_path
):
    response = _upload(
        form_client, extract_csrf_token, ("morfologia.pdf", _PDF_CONTENT, "application/pdf")
    )

    documents = _documents(auth_db)
    assert len(documents) == 1
    assert documents[0].owner_user_id == form_owner.id
    assert documents[0].original_filename == "morfologia.pdf"
    assert documents[0].processing_status == "needs_review"
    assert response.status_code == 303
    assert response.headers["location"] == f"/documents/{documents[0].id}/review"
    assert _leftover_uploads(tmp_path) == []


def test_upload_form_without_valid_csrf_is_forbidden_and_stores_nothing(
    form_client: TestClient, extract_csrf_token, auth_db: Session, tmp_path
):
    response = _upload(
        form_client,
        extract_csrf_token,
        ("morfologia.pdf", _PDF_CONTENT, "application/pdf"),
        csrf_token="forged",
    )

    assert response.status_code == 403
    assert _documents(auth_db) == []
    assert _leftover_uploads(tmp_path) == []


@pytest.mark.parametrize(
    ("file", "status_code", "message"),
    [
        (
            ("wynik.pdf", b"MZ synthetic executable", "application/pdf"),
            415,
            "Dozwolone są tylko pliki PDF, JPG i PNG",
        ),
        (("wynik.pdf", b"", "application/pdf"), 400, "Wybierz plik do przesłania."),
    ],
    ids=["disguised-executable", "empty-file"],
)
def test_upload_form_rejects_invalid_file_with_polish_message(
    form_client: TestClient,
    extract_csrf_token,
    auth_db: Session,
    tmp_path,
    file,
    status_code,
    message,
):
    response = _upload(form_client, extract_csrf_token, file)

    assert response.status_code == status_code
    assert response.text.count('role="alert"') == 1
    assert message in response.text
    assert _documents(auth_db) == []
    assert _leftover_uploads(tmp_path) == []
