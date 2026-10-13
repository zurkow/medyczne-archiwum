import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from medical_archive.models import User

_PASSWORD = "correct horse battery"
_PDF_CONTENT = b"%PDF-1.4 private document"


def _login(client: TestClient, user: User, extract_csrf_token) -> None:
    login_page = client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": user.email,
            "password": _PASSWORD,
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    csrf_token = extract_csrf_token(client.get("/").text)
    client.headers.update({"X-CSRF-Token": csrf_token})


@pytest.fixture
def document_owner(auth_db: Session) -> User:
    return _create_user(auth_db, "document-owner-access@example.test")


def _create_user(auth_db: Session, email: str) -> User:
    user = User(
        email=email,
        password_hash=PasswordHasher().hash(_PASSWORD),
        is_admin=False,
    )
    auth_db.add(user)
    auth_db.commit()
    return user


def test_documents_require_login(client: TestClient):
    response = client.get("/documents", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


def test_non_owner_and_admin_cannot_read_an_unshared_document(
    app,
    client: TestClient,
    auth_db: Session,
    document_owner: User,
    admin_user: User,
    extract_csrf_token,
    tmp_path,
):
    app.state.data_dir = tmp_path
    _login(client, document_owner, extract_csrf_token)
    upload = client.post(
        "/documents",
        files={"file": ("private.pdf", _PDF_CONTENT, "application/pdf")},
    )
    assert upload.status_code == 202
    document_id = upload.json()["id"]

    outsider_client = TestClient(app, base_url="https://testserver")
    _login(outsider_client, _create_user(auth_db, "outsider@example.test"), extract_csrf_token)
    assert outsider_client.get(f"/documents/{document_id}").status_code == 404
    assert outsider_client.get(f"/documents/{document_id}/file").status_code == 404

    admin_client = TestClient(app, base_url="https://testserver")
    _login(admin_client, admin_user, extract_csrf_token)
    assert admin_client.get(f"/documents/{document_id}").status_code == 404
    assert admin_client.get(f"/documents/{document_id}/file").status_code == 404


def test_only_ready_documents_can_be_shared_and_recipients_are_read_only(
    app,
    client: TestClient,
    auth_db: Session,
    document_owner: User,
    extract_csrf_token,
    tmp_path,
):
    app.state.data_dir = tmp_path
    _login(client, document_owner, extract_csrf_token)
    upload = client.post(
        "/documents",
        files={"file": ("private.pdf", _PDF_CONTENT, "application/pdf")},
    )
    assert upload.status_code == 202
    document_id = upload.json()["id"]
    recipient = _create_user(auth_db, "recipient@example.test")

    pending_share = client.post(
        f"/documents/{document_id}/grants",
        json={"grantee_user_id": recipient.id},
        headers={"X-CSRF-Token": extract_csrf_token(client.get("/").text)},
    )
    assert pending_share.status_code == 404

    auth_db.execute(
        text("UPDATE documents SET processing_status = 'ready' WHERE id = :document_id"),
        {"document_id": document_id},
    )
    auth_db.commit()
    csrf_token = extract_csrf_token(client.get("/").text)
    grant = client.post(
        f"/documents/{document_id}/grants",
        json={"grantee_user_id": recipient.id},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert grant.status_code == 201

    recipient_client = TestClient(app, base_url="https://testserver")
    _login(recipient_client, recipient, extract_csrf_token)
    assert recipient_client.get("/documents").json()[0]["id"] == document_id
    detail = recipient_client.get(f"/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "ready"
    download = recipient_client.get(f"/documents/{document_id}/file")
    assert download.status_code == 200
    assert download.content == _PDF_CONTENT
    assert (
        recipient_client.post(
            f"/documents/{document_id}/grants",
            json={"grantee_user_id": document_owner.id},
            headers={"X-CSRF-Token": extract_csrf_token(recipient_client.get("/").text)},
        ).status_code
        == 404
    )
    assert recipient_client.post(f"/documents/{document_id}").status_code == 405
