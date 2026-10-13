from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from medical_archive.documents.extraction import ExtractedPage, ExtractionError
from medical_archive.documents.models import Document, DocumentPage
from medical_archive.models import User

_PASSWORD = "correct horse battery"


class FakeExtractor:
    def __init__(self) -> None:
        self.pages = [ExtractedPage(1, "Wynik badania laboratoryjnego 12.10.2026", "ocr")]
        self.error: ExtractionError | None = None

    def extract(self, _path: Path) -> list[ExtractedPage]:
        if self.error is not None:
            raise self.error
        return self.pages


class FakeIndexer:
    def __init__(self) -> None:
        self.documents: set[tuple[int, str]] = set()
        self.fail = False

    def index_document(self, document: Document, _pages: list[ExtractedPage]) -> None:
        if self.fail:
            raise RuntimeError("private index details")
        self.documents.add((document.owner_user_id, document.id))

    def has_document(self, *, owner_id: int, document_id: str) -> bool:
        return (owner_id, document_id) in self.documents


@pytest.fixture
def owner_client(
    app,
    client: TestClient,
    member_user: User,
    extract_csrf_token,
    tmp_path: Path,
) -> TestClient:
    app.state.data_dir = tmp_path
    app.state.document_extractor = FakeExtractor()
    app.state.document_indexer = FakeIndexer()
    login_page = client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": member_user.email,
            "password": _PASSWORD,
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    client.headers.update({"X-CSRF-Token": extract_csrf_token(client.get("/").text)})
    return client


@pytest.fixture
def pending_scan(owner_client: TestClient, member_user: User) -> Document:
    response = owner_client.post(
        "/documents",
        files={"file": ("scan.pdf", b"%PDF-1.4 placeholder", "application/pdf")},
    )
    assert response.status_code == 202
    return Document(
        id=response.json()["id"],
        owner_user_id=member_user.id,
        storage_key="",
        original_filename="scan.pdf",
        mime_type="application/pdf",
        byte_size=1,
        processing_status="needs_review",
    )


def test_processing_suggests_type_and_date_without_changing_owner(
    owner_client: TestClient,
    pending_scan: Document,
    member_user: User,
    auth_db: Session,
):
    response = owner_client.post(f"/documents/{pending_scan.id}/process")

    assert response.status_code == 200
    assert response.json()["suggested_document_type"] == "lab_result"
    assert response.json()["suggested_document_date"] == "2026-10-12"
    assert response.json()["status"] == "needs_review"
    persisted = auth_db.get(Document, pending_scan.id)
    assert persisted.owner_user_id == member_user.id
    assert [(page.page_number, page.text) for page in persisted.pages] == [
        (1, "Wynik badania laboratoryjnego 12.10.2026")
    ]
    review_page = owner_client.get(f"/documents/{pending_scan.id}/review")
    assert review_page.status_code == 200
    assert "Wynik badania laboratoryjnego 12.10.2026" in review_page.text
    assert "Zatwierdź i indeksuj" in review_page.text
    assert f"/documents/{pending_scan.id}/preview" in review_page.text


def test_document_is_not_indexed_until_owner_approves_metadata(
    owner_client: TestClient,
    pending_scan: Document,
    member_user: User,
    app,
):
    processed = owner_client.post(f"/documents/{pending_scan.id}/process")

    assert processed.status_code == 200
    assert processed.json()["status"] == "needs_review"
    assert not app.state.document_indexer.has_document(
        owner_id=member_user.id, document_id=pending_scan.id
    )

    approved = owner_client.post(
        f"/documents/{pending_scan.id}/approve",
        json={"document_type": "lab_result", "document_date": "2026-10-01"},
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "ready"
    assert approved.json()["document_date"] == "2026-10-01"
    assert app.state.document_indexer.has_document(
        owner_id=member_user.id, document_id=pending_scan.id
    )


def test_malformed_csrf_value_is_rejected_without_internal_error(
    owner_client: TestClient,
    pending_scan: Document,
):
    owner_client.headers.pop("X-CSRF-Token", None)

    response = owner_client.post(
        f"/documents/{pending_scan.id}/process",
        json={"csrf_token": {"unexpected": "object"}},
    )

    assert response.status_code == 403


def test_ocr_failure_keeps_original_and_retry_recovers(
    owner_client: TestClient,
    pending_scan: Document,
    app,
    auth_db: Session,
    extract_csrf_token,
):
    app.state.document_extractor.error = ExtractionError(
        "unreadable_input", "The document could not be read."
    )

    failed = owner_client.post(f"/documents/{pending_scan.id}/process")

    assert failed.status_code == 422
    assert failed.json()["status"] == "ocr_failed"
    review_page = owner_client.get(f"/documents/{pending_scan.id}/review")
    assert "Ponów rozpoznawanie" in review_page.text
    assert "Rozpoznaj tekst" not in review_page.text
    owner_client.headers.update({"X-CSRF-Token": extract_csrf_token(review_page.text)})
    original = owner_client.get(f"/documents/{pending_scan.id}/file")
    assert original.status_code == 200
    assert original.content == b"%PDF-1.4 placeholder"
    assert auth_db.get(Document, pending_scan.id).processing_status == "ocr_failed"

    app.state.document_extractor.error = None
    retried = owner_client.post(f"/documents/{pending_scan.id}/retry")

    assert retried.status_code == 200
    assert retried.json()["status"] == "needs_review"
    assert auth_db.query(DocumentPage).filter_by(document_id=pending_scan.id).count() == 1


def test_index_failure_preserves_original_text_and_can_be_retried(
    owner_client: TestClient,
    pending_scan: Document,
    app,
    auth_db: Session,
):
    owner_client.post(f"/documents/{pending_scan.id}/process")
    app.state.document_indexer.fail = True

    failed = owner_client.post(
        f"/documents/{pending_scan.id}/approve",
        json={"document_type": "lab_result", "document_date": "2026-10-01"},
    )

    assert failed.status_code == 503
    assert failed.json()["status"] == "index_failed"
    assert "private index details" not in failed.text
    assert auth_db.query(DocumentPage).filter_by(document_id=pending_scan.id).count() == 1
    assert owner_client.get(f"/documents/{pending_scan.id}/file").status_code == 200

    app.state.document_indexer.fail = False
    retried = owner_client.post(f"/documents/{pending_scan.id}/retry")

    assert retried.status_code == 200
    assert retried.json()["status"] == "ready"


def test_shared_recipient_cannot_process_or_approve_document(
    owner_client: TestClient,
    pending_scan: Document,
    app,
    auth_db: Session,
    extract_csrf_token,
    admin_user: User,
):
    owner_client.post(f"/documents/{pending_scan.id}/process")
    owner_client.post(
        f"/documents/{pending_scan.id}/approve",
        json={"document_type": "lab_result", "document_date": "2026-10-01"},
    )
    recipient = User(
        email="document-recipient@example.test",
        password_hash=PasswordHasher().hash(_PASSWORD),
        is_admin=False,
    )
    auth_db.add(recipient)
    auth_db.commit()
    recipient_id = recipient.id
    grant = owner_client.post(
        f"/documents/{pending_scan.id}/grants",
        json={"grantee_user_id": recipient_id},
    )
    assert grant.status_code == 201
    recipient_client = TestClient(app, base_url="https://testserver")
    login_page = recipient_client.get("/auth/login")
    login = recipient_client.post(
        "/auth/login",
        data={
            "email": recipient.email,
            "password": _PASSWORD,
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    recipient_client.headers.update(
        {"X-CSRF-Token": extract_csrf_token(recipient_client.get("/").text)}
    )

    assert recipient_client.post(f"/documents/{pending_scan.id}/process").status_code == 404
    assert (
        recipient_client.post(
            f"/documents/{pending_scan.id}/approve",
            json={"document_type": "lab_result", "document_date": "2026-10-01"},
        ).status_code
        == 404
    )

    admin_client = TestClient(app, base_url="https://testserver")
    admin_login_page = admin_client.get("/auth/login")
    admin_login = admin_client.post(
        "/auth/login",
        data={
            "email": admin_user.email,
            "password": _PASSWORD,
            "csrf_token": extract_csrf_token(admin_login_page.text),
        },
        follow_redirects=False,
    )
    assert admin_login.status_code == 303
    admin_client.headers.update({"X-CSRF-Token": extract_csrf_token(admin_client.get("/").text)})
    assert admin_client.post(f"/documents/{pending_scan.id}/process").status_code == 404
    assert (
        admin_client.post(
            f"/documents/{pending_scan.id}/approve",
            json={"document_type": "lab_result", "document_date": "2026-10-01"},
        ).status_code
        == 404
    )
