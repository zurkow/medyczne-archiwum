import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from medical_archive.models import User

_PASSWORD = "correct horse battery"


def _create_user(auth_db: Session, email: str, *, is_admin: bool = False) -> User:
    user = User(
        email=email,
        password_hash=PasswordHasher().hash(_PASSWORD),
        is_admin=is_admin,
    )
    auth_db.add(user)
    auth_db.commit()
    return user


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


def _create_measurement(client: TestClient, *, measured_at: str, name: str) -> dict:
    response = client.post(
        "/measurements",
        json={
            "name": name,
            "value": "7.25",
            "unit": "u",
            "measured_at": measured_at,
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def owner_client(client: TestClient, member_user: User, extract_csrf_token):
    _login(client, member_user, extract_csrf_token)
    return client


def test_owner_can_create_read_update_and_delete_measurement(
    owner_client: TestClient, extract_csrf_token
):
    measurement = _create_measurement(
        owner_client, measured_at="2026-10-12T08:30:00Z", name="Synthetic value"
    )
    measurement_id = measurement["id"]

    listed = owner_client.get("/measurements")
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()] == [measurement_id]
    assert owner_client.get(f"/measurements/{measurement_id}").status_code == 200

    updated = owner_client.put(
        f"/measurements/{measurement_id}",
        json={
            "name": "Updated synthetic value",
            "value": "8.5",
            "unit": "u",
            "measured_at": "2026-10-12T09:30:00Z",
            "note": "updated synthetic test entry",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["value"] == 8.5
    assert updated.json()["name"] == "Updated synthetic value"

    deleted = owner_client.delete(
        f"/measurements/{measurement_id}",
        headers={"X-CSRF-Token": extract_csrf_token(owner_client.get("/").text)},
    )
    assert deleted.status_code == 204
    assert owner_client.get(f"/measurements/{measurement_id}").status_code == 404


def test_measurement_list_has_bounded_time_pagination_and_stable_order(
    owner_client: TestClient,
):
    timestamp = "2026-10-12T08:30:00Z"
    measurements = [
        _create_measurement(owner_client, measured_at=timestamp, name=f"Synthetic {index}")
        for index in range(3)
    ]
    old_measurement = _create_measurement(
        owner_client, measured_at="2020-10-12T08:30:00Z", name="Old synthetic value"
    )
    expected_ids = sorted(measurement["id"] for measurement in measurements)

    first_page = owner_client.get("/measurements?days=365&limit=2")
    second_page = owner_client.get("/measurements?days=365&limit=2&offset=2")

    assert first_page.status_code == second_page.status_code == 200
    assert [item["id"] for item in first_page.json()] == expected_ids[:2]
    assert [item["id"] for item in second_page.json()] == expected_ids[2:]
    assert old_measurement["id"] not in {
        item["id"] for item in first_page.json() + second_page.json()
    }
    assert owner_client.get("/measurements?limit=101").status_code == 422
    assert owner_client.get("/measurements?days=3651").status_code == 422


def test_other_users_and_admin_cannot_access_unshared_measurement(
    app,
    owner_client: TestClient,
    auth_db: Session,
    admin_user: User,
    extract_csrf_token,
):
    measurement = _create_measurement(
        owner_client, measured_at="2026-10-12T08:30:00Z", name="Private synthetic value"
    )
    outsider = _create_user(auth_db, f"outsider-{uuid.uuid4()}@example.test")
    outsider_client = TestClient(app, base_url="https://testserver")
    _login(outsider_client, outsider, extract_csrf_token)
    admin_client = TestClient(app, base_url="https://testserver")
    _login(admin_client, admin_user, extract_csrf_token)

    for unauthorized_client in (outsider_client, admin_client):
        assert unauthorized_client.get("/measurements").json() == []
        assert unauthorized_client.get(f"/measurements/{measurement['id']}").status_code == 404
        assert (
            unauthorized_client.put(
                f"/measurements/{measurement['id']}",
                json={
                    "name": "attempted change",
                    "value": "9",
                    "unit": "u",
                    "measured_at": "2026-10-12T08:30:00Z",
                },
            ).status_code
            == 404
        )
        assert unauthorized_client.delete(f"/measurements/{measurement['id']}").status_code == 404


def test_explicit_grantee_can_read_but_cannot_mutate_measurement(
    app,
    owner_client: TestClient,
    auth_db: Session,
    member_user: User,
    extract_csrf_token,
):
    measurement = _create_measurement(
        owner_client, measured_at="2026-10-12T08:30:00Z", name="Shared synthetic value"
    )
    recipient = _create_user(auth_db, f"recipient-{uuid.uuid4()}@example.test")
    grant = owner_client.post(
        f"/measurements/{measurement['id']}/grants",
        json={"grantee_user_id": recipient.id},
    )
    assert grant.status_code == 201

    recipient_client = TestClient(app, base_url="https://testserver")
    _login(recipient_client, recipient, extract_csrf_token)
    assert recipient_client.get("/measurements").json()[0]["id"] == measurement["id"]
    assert recipient_client.get(f"/measurements/{measurement['id']}").status_code == 200
    assert (
        recipient_client.put(
            f"/measurements/{measurement['id']}",
            json={
                "name": "attempted change",
                "value": "9",
                "unit": "u",
                "measured_at": "2026-10-12T08:30:00Z",
            },
        ).status_code
        == 404
    )
    assert recipient_client.delete(f"/measurements/{measurement['id']}").status_code == 404
    assert (
        recipient_client.post(
            f"/measurements/{measurement['id']}/grants",
            json={"grantee_user_id": member_user.id},
        ).status_code
        == 404
    )

    revoked = owner_client.delete(
        f"/measurements/{measurement['id']}/grants/{recipient.id}",
        headers={"X-CSRF-Token": extract_csrf_token(owner_client.get("/").text)},
    )
    assert revoked.status_code == 204
    assert recipient_client.get(f"/measurements/{measurement['id']}").status_code == 404
