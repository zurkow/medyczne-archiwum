from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from medical_archive.models import AccessGrant, Document, Measurement, User
from medical_archive.rag.service import GenerationError

_PASSWORD = "correct horse battery"


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
    client.headers.update({"X-CSRF-Token": extract_csrf_token(client.get("/").text)})


def _document(*, owner_id: int, filename: str, ready: bool = True) -> Document:
    return Document(
        id=str(uuid4()),
        owner_user_id=owner_id,
        storage_key=str(uuid4()),
        original_filename=filename,
        mime_type="application/pdf",
        byte_size=1,
        document_type="synthetic",
        document_date=datetime.now(UTC).date(),
        processing_status="ready" if ready else "needs_review",
        ocr_status="complete",
    )


def _measurement(*, owner_id: int, name: str, value: str) -> Measurement:
    return Measurement(
        id=str(uuid4()),
        owner_user_id=owner_id,
        name=name,
        value=Decimal(value),
        unit="synthetic-unit",
        measured_at_utc=datetime.now(UTC),
    )


def test_dashboard_shows_only_owned_and_explicitly_shared_records(
    client: TestClient,
    auth_db: Session,
    member_user: User,
    extract_csrf_token,
):
    alice_document = _document(owner_id=member_user.id, filename="alice-synthetic-result.pdf")
    alice_measurement = _measurement(
        owner_id=member_user.id, name="Alice synthetic marker", value="12.5"
    )
    bob = User(
        email=f"bob-{uuid4()}@example.test",
        password_hash=PasswordHasher().hash(_PASSWORD),
        is_admin=False,
    )
    auth_db.add(bob)
    auth_db.flush()
    shared_document = _document(owner_id=bob.id, filename="shared-synthetic-result.pdf")
    private_document = _document(owner_id=bob.id, filename="private data belonging to bob")
    shared_measurement = _measurement(owner_id=bob.id, name="Shared synthetic marker", value="8.0")
    private_measurement = _measurement(
        owner_id=bob.id, name="Bob private synthetic marker", value="9.0"
    )
    auth_db.add_all(
        [
            alice_document,
            alice_measurement,
            shared_document,
            private_document,
            shared_measurement,
            private_measurement,
        ]
    )
    auth_db.flush()
    auth_db.add_all(
        [
            AccessGrant(
                owner_user_id=bob.id,
                grantee_user_id=member_user.id,
                resource_type="document",
                resource_id=shared_document.id,
            ),
            AccessGrant(
                owner_user_id=bob.id,
                grantee_user_id=member_user.id,
                resource_type="measurement",
                resource_id=shared_measurement.id,
            ),
        ]
    )
    auth_db.commit()
    _login(client, member_user, extract_csrf_token)

    response = client.get("/")

    assert response.status_code == 200
    assert "Ostatnie dokumenty" in response.text
    assert "Pomiary" in response.text
    assert "Zapytaj o swoje dokumenty" in response.text
    assert member_user.email in response.text
    assert "alice-synthetic-result.pdf" in response.text
    assert "shared-synthetic-result.pdf" in response.text
    assert "Alice synthetic marker" in response.text
    assert "Shared synthetic marker" in response.text
    assert "private data belonging to bob" not in response.text
    assert "Bob private synthetic marker" not in response.text
    assert "/measurements/ui" in response.text
    assert "/assistant/ask" in response.text
    assert alice_measurement.id not in response.text
    assert private_measurement.id not in response.text


def test_dashboard_question_shows_local_model_failure_without_fabricating_answer(
    client: TestClient,
    member_user: User,
    extract_csrf_token,
):
    class UnavailableAssistant:
        def ask(self, *, user_id: int, question: str):
            raise GenerationError("synthetic private connection detail")

    client.app.state.answer_service = UnavailableAssistant()
    _login(client, member_user, extract_csrf_token)
    dashboard = client.get("/")
    client.headers.pop("X-CSRF-Token", None)

    response = client.post(
        "/assistant/ask",
        data={
            "csrf_token": extract_csrf_token(dashboard.text),
            "question": "Synthetic question?",
        },
    )

    assert response.status_code == 503
    assert "Lokalny model jest niedostępny" in response.text
    assert 'role="alert"' in response.text
    assert "synthetic private connection detail" not in response.text
    assert "Odpowiedź" not in response.text
