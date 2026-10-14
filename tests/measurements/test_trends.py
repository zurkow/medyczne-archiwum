from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from medical_archive.models import AccessGrant, Measurement, User


@pytest.fixture
def owner_client(client: TestClient, member_user: User, extract_csrf_token):
    login_page = client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": member_user.email,
            "password": "correct horse battery",
            "csrf_token": extract_csrf_token(login_page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    client.headers.update({"X-CSRF-Token": extract_csrf_token(client.get("/").text)})
    return client


def _create_measurement(
    client: TestClient,
    *,
    name: str,
    unit: str,
    measured_at: datetime,
    value: str = "120",
) -> dict:
    response = client.post(
        "/measurements",
        json={
            "name": name,
            "unit": unit,
            "value": value,
            "measured_at": measured_at.isoformat(),
        },
    )
    assert response.status_code == 201
    return response.json()


def test_trend_filters_exact_name_and_unit_and_sorts_by_utc_then_id(
    owner_client: TestClient,
):
    timestamp = datetime.now(UTC).replace(microsecond=0)
    earliest = _create_measurement(
        owner_client, name="Synthetic pulse", unit="bpm", measured_at=timestamp - timedelta(days=2)
    )
    tied = [
        _create_measurement(owner_client, name="Synthetic pulse", unit="bpm", measured_at=timestamp)
        for _ in range(2)
    ]
    _create_measurement(
        owner_client, name="Synthetic pulse", unit="count/min", measured_at=timestamp
    )
    _create_measurement(owner_client, name="Synthetic pressure", unit="bpm", measured_at=timestamp)

    response = owner_client.get(
        "/measurements/trends",
        params={"name": "Synthetic pulse", "unit": "bpm", "days": 30},
    )

    assert response.status_code == 200, response.text
    points = response.json()["points"]
    assert [point["id"] for point in points] == [
        earliest["id"],
        *sorted(point["id"] for point in tied),
    ]
    assert {(point["name"], point["unit"]) for point in points} == {("Synthetic pulse", "bpm")}
    assert [point["measured_at"] for point in points] == sorted(
        point["measured_at"] for point in points
    )


def test_trend_contains_only_owned_and_explicitly_shared_points(
    owner_client: TestClient,
    auth_db: Session,
    member_user: User,
    admin_user: User,
    extract_csrf_token,
):
    timestamp = datetime.now(UTC).replace(microsecond=0)
    own = _create_measurement(
        owner_client, name="Synthetic weight", unit="kg", measured_at=timestamp
    )
    owner = User(
        email=f"trend-owner-{uuid4()}@example.test",
        password_hash="synthetic-test-hash",
        is_admin=False,
    )
    auth_db.add(owner)
    auth_db.flush()
    shared = Measurement(
        id=str(uuid4()),
        owner_user_id=owner.id,
        name="Synthetic weight",
        value=Decimal(70),
        unit="kg",
        measured_at_utc=timestamp,
    )
    private = Measurement(
        id=str(uuid4()),
        owner_user_id=owner.id,
        name="Synthetic weight",
        value=Decimal(71),
        unit="kg",
        measured_at_utc=timestamp,
    )
    auth_db.add_all([shared, private])
    auth_db.flush()
    auth_db.add(
        AccessGrant(
            owner_user_id=owner.id,
            grantee_user_id=member_user.id,
            resource_type="measurement",
            resource_id=shared.id,
        )
    )
    auth_db.commit()

    response = owner_client.get(
        "/measurements/trends",
        params={"name": "Synthetic weight", "unit": "kg", "days": 30},
    )

    assert response.status_code == 200, response.text
    assert {point["id"] for point in response.json()["points"]} == {own["id"], shared.id}
    assert private.id not in response.text

    admin_client = TestClient(owner_client.app, base_url="https://testserver")
    admin_login_page = admin_client.get("/auth/login")
    admin_login = admin_client.post(
        "/auth/login",
        data={
            "email": admin_user.email,
            "password": "correct horse battery",
            "csrf_token": extract_csrf_token(admin_login_page.text),
        },
        follow_redirects=False,
    )
    assert admin_login.status_code == 303
    admin_response = admin_client.get(
        "/measurements/trends",
        params={"name": "Synthetic weight", "unit": "kg", "days": 30},
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["points"] == []
    assert (
        admin_client.post(
            f"/measurements/{own['id']}/delete",
            data={"csrf_token": "invalid"},
        ).status_code
        == 404
    )


def test_trend_allows_only_supported_day_ranges_and_bounds_point_count(
    owner_client: TestClient,
    auth_db: Session,
    member_user: User,
):
    now = datetime.now(UTC).replace(microsecond=0)
    auth_db.add_all(
        [
            Measurement(
                id=str(uuid4()),
                owner_user_id=member_user.id,
                name="Synthetic capped series",
                value=Decimal(index),
                unit="u",
                measured_at_utc=now - timedelta(seconds=index),
            )
            for index in range(205)
        ]
    )
    auth_db.commit()

    for days in (7, 30, 90, 365):
        response = owner_client.get(
            "/measurements/trends",
            params={"name": "Synthetic capped series", "unit": "u", "days": days},
        )
        assert response.status_code == 200
        assert len(response.json()["points"]) <= 200

    assert (
        owner_client.get(
            "/measurements/trends",
            params={"name": "Synthetic capped series", "unit": "u", "days": 14},
        ).status_code
        == 422
    )
    assert (
        owner_client.get(
            "/measurements/trends",
            params={
                "name": "Synthetic capped series",
                "unit": "u",
                "days": 30,
                "limit": 501,
            },
        ).status_code
        == 422
    )


def test_measurement_page_has_accessible_svg_table_fallback_and_owner_forms(
    owner_client: TestClient,
):
    measurement = _create_measurement(
        owner_client,
        name="Synthetic temperature",
        unit="degC",
        measured_at=datetime.now(UTC).replace(microsecond=0),
    )

    response = owner_client.get("/measurements/ui?days=30")

    assert response.status_code == 200
    assert "Synthetic temperature" in response.text
    assert 'role="img"' in response.text
    assert "<title>" in response.text
    assert "<desc " in response.text
    assert "<table" in response.text
    assert "Data i godzina (UTC)" in response.text
    assert f"/measurements/{measurement['id']}/edit" in response.text
    assert f"/measurements/{measurement['id']}/delete" in response.text
    assert "<script" not in response.text


def test_measurement_page_rejects_unsupported_history_ranges(owner_client: TestClient):
    assert owner_client.get("/measurements/ui?days=14").status_code == 422


def test_measurement_form_creates_updates_and_deletes_owner_record(
    owner_client: TestClient,
    extract_csrf_token,
):
    form_page = owner_client.get("/measurements/ui")
    csrf_token = extract_csrf_token(form_page.text)
    create_response = owner_client.post(
        "/measurements/ui",
        data={
            "csrf_token": csrf_token,
            "name": "Synthetic form entry",
            "value": "4.5",
            "unit": "u",
            "measured_at": datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M"),
            "note": "synthetic form note",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 303
    measurement = next(
        item
        for item in owner_client.get("/measurements?days=365").json()
        if item["name"] == "Synthetic form entry"
    )
    resource_id = measurement["id"]

    edit_page = owner_client.get("/measurements/ui")
    update_response = owner_client.post(
        f"/measurements/{resource_id}/edit",
        data={
            "csrf_token": extract_csrf_token(edit_page.text),
            "name": "Updated synthetic form entry",
            "value": "5.5",
            "unit": "u",
            "measured_at": datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M"),
            "note": "",
        },
        follow_redirects=False,
    )
    assert update_response.status_code == 303
    assert owner_client.get(f"/measurements/{resource_id}").json()["name"] == (
        "Updated synthetic form entry"
    )

    delete_page = owner_client.get("/measurements/ui")
    delete_response = owner_client.post(
        f"/measurements/{resource_id}/delete",
        data={"csrf_token": extract_csrf_token(delete_page.text)},
        follow_redirects=False,
    )
    assert delete_response.status_code == 303
    assert owner_client.get(f"/measurements/{resource_id}").status_code == 404
