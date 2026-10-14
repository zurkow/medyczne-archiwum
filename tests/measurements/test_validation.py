from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from medical_archive.models import User

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


@pytest.fixture
def owner_client(client: TestClient, member_user: User, extract_csrf_token):
    _login(client, member_user, extract_csrf_token)
    return client


def test_create_measurement_normalizes_value_and_timestamp(
    owner_client: TestClient, auth_db, member_user: User
):
    response = owner_client.post(
        "/measurements",
        json={
            "name": "Ciśnienie skurczowe",
            "value": "125.50000000",
            "unit": "mmHg",
            "measured_at": "2026-10-12T08:30:00+02:00",
            "note": "synthetic test entry",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["value"] == 125.5
    assert payload["unit"] == "mmHg"
    assert payload["measured_at"] == "2026-10-12T06:30:00Z"
    stored = auth_db.execute(
        text(
            "SELECT owner_user_id, value, measured_at_utc "
            "FROM measurements WHERE id = :measurement_id"
        ),
        {"measurement_id": payload["id"]},
    ).one()
    assert stored.owner_user_id == member_user.id
    assert Decimal(str(stored.value)) == Decimal("125.50000000")
    assert stored.measured_at_utc == "2026-10-12 06:30:00.000000"


@pytest.mark.parametrize(
    ("value", "unit", "field"),
    [
        ("NaN", "mmHg", "value"),
        ("Infinity", "mmHg", "value"),
        ("-Infinity", "mmHg", "value"),
        ("1000000000000", "mmHg", "value"),
        ("1.123456789", "mmHg", "value"),
        (1.5, "mmHg", "value"),
        ("1", " ", "unit"),
    ],
)
def test_measurement_rejects_invalid_numeric_values_and_blank_unit(
    owner_client: TestClient, value: str | float, unit: str, field: str
):
    response = owner_client.post(
        "/measurements",
        json={
            "name": "Synthetic measurement",
            "value": value,
            "unit": unit,
            "measured_at": "2026-10-12T08:30:00Z",
        },
    )

    assert response.status_code == 422
    assert field in str(response.json()["detail"])


def test_measurement_rejects_naive_timestamp(owner_client: TestClient):
    response = owner_client.post(
        "/measurements",
        json={
            "name": "Synthetic measurement",
            "value": "1",
            "unit": "u",
            "measured_at": "2026-10-12T08:30:00",
        },
    )

    assert response.status_code == 422
    assert "measured_at" in str(response.json()["detail"])


@pytest.mark.parametrize(
    ("name", "unit", "note", "field"),
    [
        ("   ", "u", None, "name"),
        ("n" * 101, "u", None, "name"),
        ("measurement", "u" * 33, None, "unit"),
        ("measurement", "u", "n" * 1001, "note"),
    ],
)
def test_measurement_rejects_blank_or_oversized_text_fields(
    owner_client: TestClient, name: str, unit: str, note: str | None, field: str
):
    response = owner_client.post(
        "/measurements",
        json={
            "name": name,
            "value": "1",
            "unit": unit,
            "measured_at": "2026-10-12T08:30:00Z",
            "note": note,
        },
    )

    assert response.status_code == 422
    assert field in str(response.json()["detail"])
