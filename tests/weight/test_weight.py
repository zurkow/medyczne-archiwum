from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.models import User, WeightEntry
from medical_archive.weight.service import (
    NORMAL,
    OVERWEIGHT,
    UNDERWEIGHT,
    WeightError,
    WeightRow,
    bmi_category,
    bmi_chart,
    calculate_bmi,
    parse_height_cm,
    parse_measured_on,
    parse_weight_kg,
)

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


def _post(client: TestClient, path: str, extract_csrf_token, **fields: str):
    token = extract_csrf_token(client.get("/weight/ui").text)
    return client.post(path, data={"csrf_token": token, **fields}, follow_redirects=False)


@pytest.fixture
def member_client(client: TestClient, member_user: User, extract_csrf_token):
    _login(client, member_user, extract_csrf_token)
    return client


def test_bmi_is_calculated_from_weight_and_height_rounded_to_one_decimal():
    assert calculate_bmi(Decimal(70), Decimal(175)) == Decimal("22.9")
    assert calculate_bmi(Decimal("80.5"), Decimal(180)) == Decimal("24.8")


@pytest.mark.parametrize(
    ("bmi", "expected"),
    [
        ("18.4", UNDERWEIGHT),
        ("18.5", NORMAL),
        ("24.9", NORMAL),
        ("25.0", OVERWEIGHT),
    ],
)
def test_bmi_category_uses_underweight_normal_and_overweight_ranges(bmi, expected):
    assert bmi_category(Decimal(bmi)) == expected


@pytest.mark.parametrize("raw", ["", "abc", "NaN", "Infinity", "19.9", "500.1", "70.55", "-70"])
def test_invalid_weight_is_rejected(raw):
    with pytest.raises(WeightError):
        parse_weight_kg(raw)


def test_weight_and_height_accept_decimal_comma():
    assert parse_weight_kg("70,5") == Decimal("70.5")
    assert parse_height_cm("178") == Decimal(178)


@pytest.mark.parametrize("raw", ["49.9", "250.1", "x", ""])
def test_invalid_height_is_rejected(raw):
    with pytest.raises(WeightError):
        parse_height_cm(raw)


def test_future_or_malformed_date_is_rejected():
    today = date(2026, 10, 21)
    assert parse_measured_on("2026-10-21", today=today) == today
    for raw in ("2026-10-22", "21.10.2026", ""):
        with pytest.raises(WeightError):
            parse_measured_on(raw, today=today)


def test_chart_marks_bands_and_places_points_by_bmi():
    rows = [
        WeightRow("a", date(2026, 10, 1), Decimal(70), Decimal("22.9"), NORMAL),
        WeightRow("b", date(2026, 10, 11), Decimal(85), Decimal("27.8"), OVERWEIGHT),
        WeightRow("c", date(2026, 10, 12), Decimal(60), None, None),
    ]

    chart = bmi_chart(rows)

    assert [band["category"] for band in chart["bands"]] == [OVERWEIGHT, NORMAL, UNDERWEIGHT]
    first, second = chart["points"]
    assert first["x"] < second["x"]
    assert second["y"] < first["y"]
    assert bmi_chart([rows[2]]) is None


def test_page_shows_green_normal_bmi_after_saving_height_and_weight(
    member_client: TestClient, extract_csrf_token
):
    assert "Podaj wzrost" in member_client.get("/weight/ui").text

    saved_height = _post(member_client, "/weight/height", extract_csrf_token, height_cm="175")
    saved_weight = _post(
        member_client,
        "/weight/ui",
        extract_csrf_token,
        weight_kg="70,0",
        measured_on="2026-10-01",
    )
    page = member_client.get("/weight/ui").text

    assert saved_height.status_code == saved_weight.status_code == 303
    assert "22.9" in page
    assert "BMI prawidłowe" in page
    assert "bmi-band-normal" in page
    assert "bmi-normal" in page


def test_date_defaults_to_today_in_the_form(member_client: TestClient):
    page = member_client.get("/weight/ui").text

    assert 'name="measured_on" type="date" value="20' in page


def test_invalid_submission_shows_alert_and_stores_nothing(
    member_client: TestClient, auth_db: Session, extract_csrf_token
):
    response = _post(
        member_client,
        "/weight/ui",
        extract_csrf_token,
        weight_kg="abc",
        measured_on="2026-10-01",
    )

    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert auth_db.scalars(select(WeightEntry)).all() == []


def test_post_without_valid_csrf_token_is_rejected(member_client: TestClient):
    response = member_client.post(
        "/weight/ui",
        data={"csrf_token": "wrong", "weight_kg": "70", "measured_on": "2026-10-01"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_weight_pages_require_login(client: TestClient):
    response = client.get("/weight/ui", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


def test_other_users_and_admin_cannot_see_or_delete_private_weight(
    app,
    member_client: TestClient,
    auth_db: Session,
    admin_user: User,
    extract_csrf_token,
):
    _post(
        member_client,
        "/weight/ui",
        extract_csrf_token,
        weight_kg="71.3",
        measured_on="2026-10-01",
    )
    entry = auth_db.scalars(select(WeightEntry)).one()
    admin_client = TestClient(app, base_url="https://testserver")
    _login(admin_client, admin_user, extract_csrf_token)

    page = admin_client.get("/weight/ui")
    deleted = _post(admin_client, f"/weight/{entry.id}/delete", extract_csrf_token)

    assert "71.3" not in page.text
    assert deleted.status_code == 404
    assert auth_db.get(WeightEntry, entry.id) is not None


def test_owner_can_delete_entry(member_client: TestClient, auth_db: Session, extract_csrf_token):
    _post(
        member_client,
        "/weight/ui",
        extract_csrf_token,
        weight_kg="71.3",
        measured_on="2026-10-01",
    )
    entry_id = auth_db.scalars(select(WeightEntry)).one().id

    response = _post(member_client, f"/weight/{entry_id}/delete", extract_csrf_token)

    assert response.status_code == 303
    auth_db.expire_all()
    assert auth_db.get(WeightEntry, entry_id) is None
