from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import (
    get_authenticated_session,
    get_current_user,
    require_resource_access,
    require_resource_owner,
)
from medical_archive.auth.grant_router import create_grant_router
from medical_archive.auth.grants import can_access, visible_resource_ids
from medical_archive.auth.service import rotate_csrf, verify_csrf
from medical_archive.db import get_session
from medical_archive.measurements.models import Measurement
from medical_archive.measurements.service import (
    create_measurement,
    delete_measurement,
    public_measurement,
    update_measurement,
)
from medical_archive.measurements.trends import (
    ALLOWED_HISTORY_DAYS,
    MAX_HISTORY_RECORDS,
    MAX_TREND_POINTS,
    chart_data,
    measurement_history,
    trend_points,
)
from medical_archive.models import LoginSession, User
from medical_archive.web.templates import render_template

router = APIRouter(prefix="/measurements", tags=["measurements"])
_MAX_FORM_BYTES = 8192


class MeasurementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    value: Decimal
    unit: str = Field(min_length=1, max_length=32)
    measured_at: datetime
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "unit", mode="before")
    @classmethod
    def strip_required_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("value", mode="before")
    @classmethod
    def validate_decimal_value(cls, value: Any) -> Decimal:
        if isinstance(value, (bool, float)):
            raise PydanticCustomError("decimal_type", "value must be a decimal string or integer.")
        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError("value must be a valid decimal.") from error
        if not decimal_value.is_finite():
            raise ValueError("value must be finite.")
        integer_digits = max(decimal_value.adjusted() + 1, 0)
        decimal_places = max(-decimal_value.as_tuple().exponent, 0)
        if integer_digits > 12:
            raise ValueError("value must have at most 12 integer digits.")
        if decimal_places > 8:
            raise ValueError("value must have at most 8 decimal places.")
        return decimal_value

    @field_validator("measured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must include a timezone.")
        return value.astimezone(UTC)


def _lookup_owner_id(session: Session, measurement_id: str) -> int | None:
    measurement = session.get(Measurement, measurement_id)
    return measurement.owner_user_id if measurement is not None else None


_require_measurement_access = require_resource_access(
    resource_type="measurement",
    get_owner_id=_lookup_owner_id,
)
_require_measurement_owner = require_resource_owner(
    resource_type="measurement",
    get_owner_id=_lookup_owner_id,
)


def _require_csrf(
    user: User,
    login_session: LoginSession,
    request: Request,
) -> None:
    if login_session.user_id != user.id or not verify_csrf(
        login_session, request.headers.get("x-csrf-token")
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


async def _form_fields(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", maxsplit=1)[0] != (
        "application/x-www-form-urlencoded"
    ):
        raise HTTPException(status_code=415, detail="Unsupported form content type.")
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Form data is too large.")
    try:
        parsed = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=8,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid form data.") from error
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(status_code=400, detail="Duplicate form fields are not allowed.")
    return {key: values[0] for key, values in parsed.items()}


def _verify_form_csrf(
    user: User,
    login_session: LoginSession,
    request: Request,
    fields: dict[str, str],
) -> None:
    csrf_token = fields.get("csrf_token") or request.headers.get("x-csrf-token")
    if login_session.user_id != user.id or not verify_csrf(login_session, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


def _form_measurement_input(fields: dict[str, str]) -> MeasurementInput:
    try:
        measured_at = datetime.fromisoformat(fields.get("measured_at", ""))
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="Data i godzina (UTC): podaj prawidłowy termin.",
        ) from error
    if measured_at.tzinfo is None or measured_at.utcoffset() is None:
        measured_at = measured_at.replace(tzinfo=UTC)

    try:
        return MeasurementInput.model_validate(
            {
                "name": fields.get("name", ""),
                "value": fields.get("value", ""),
                "unit": fields.get("unit", ""),
                "measured_at": measured_at,
                "note": fields.get("note") or None,
            }
        )
    except ValidationError as error:
        first_error = error.errors()[0]
        field = str(first_error["loc"][0])
        label = {
            "name": "Nazwa",
            "value": "Wartość",
            "unit": "Jednostka",
            "measured_at": "Data i godzina (UTC)",
            "note": "Notatka",
        }.get(field, "Pomiar")
        raise HTTPException(
            status_code=422,
            detail=f"{label}: {first_error['msg']}",
        ) from error


def _trend_point_payload(point) -> dict[str, str]:
    return {
        "id": point.id,
        "name": point.name,
        "value": str(point.value),
        "unit": point.unit,
        "measured_at": point.measured_at.isoformat().replace("+00:00", "Z"),
    }


def _form_datetime_value(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S")


def _require_supported_range(days: int) -> None:
    if days not in ALLOWED_HISTORY_DAYS:
        raise HTTPException(
            status_code=422,
            detail="History range must be 7, 30, 90, or 365 days.",
        )


def _lookup_grantable_owner_id(session: Session, measurement_id: str) -> int | None:
    measurement = session.get(Measurement, measurement_id)
    return measurement.owner_user_id if measurement is not None else None


grant_router = create_grant_router(
    resource_type="measurement",
    get_owner_id=_lookup_grantable_owner_id,
)


@router.post("", status_code=status.HTTP_201_CREATED)
def create(
    body: MeasurementInput,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str | float | None]:
    _require_csrf(user, login_session, request)
    measurement = create_measurement(
        session,
        owner_user_id=user.id,
        name=body.name,
        value=body.value,
        unit=body.unit,
        measured_at=body.measured_at,
        note=body.note,
    )
    return public_measurement(measurement)


@router.get("")
def list_measurements(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=3650)] = 365,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> list[dict[str, str | float | None]]:
    owned_resource_ids = session.scalars(
        select(Measurement.id).where(Measurement.owner_user_id == user.id)
    ).all()
    visible_ids = visible_resource_ids(
        session,
        user_id=user.id,
        resource_type="measurement",
        owned_resource_ids=owned_resource_ids,
    )
    if not visible_ids:
        return []

    cutoff = datetime.now(UTC) - timedelta(days=days)
    measurements = session.scalars(
        select(Measurement)
        .where(
            Measurement.id.in_(visible_ids),
            Measurement.measured_at_utc >= cutoff,
        )
        .order_by(Measurement.measured_at_utc.desc(), Measurement.id)
        .offset(offset)
        .limit(limit)
    ).all()
    visible_measurements = [
        measurement
        for measurement in measurements
        if measurement.owner_user_id == user.id
        or can_access(
            session,
            user_id=user.id,
            resource_type="measurement",
            resource_id=measurement.id,
            owner_user_id=measurement.owner_user_id,
        )
    ]
    return [public_measurement(measurement) for measurement in visible_measurements]


@router.get("/trends")
def measurement_trends(
    name: Annotated[str, Query(min_length=1, max_length=100)],
    unit: Annotated[str, Query(min_length=1, max_length=32)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=MAX_TREND_POINTS)] = MAX_TREND_POINTS,
) -> dict[str, object]:
    _require_supported_range(days)
    points = trend_points(
        session,
        user_id=user.id,
        name=name,
        unit=unit,
        days=days,
        limit=limit,
    )
    return {
        "name": name,
        "unit": unit,
        "days": days,
        "points": [_trend_point_payload(point) for point in points],
    }


@router.get("/ui", include_in_schema=False)
def measurement_history_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    series: Annotated[int | None, Query(ge=0)] = None,
):
    _require_supported_range(days)
    csrf_token = rotate_csrf(login_session)
    session.commit()
    records = measurement_history(
        session,
        user_id=user.id,
        days=days,
        limit=MAX_HISTORY_RECORDS,
    )
    series_options: list[dict[str, str | int]] = []
    seen_series: set[tuple[str, str]] = set()
    for point in records:
        key = (point.name, point.unit)
        if key not in seen_series:
            seen_series.add(key)
            series_options.append(
                {"index": len(series_options), "name": point.name, "unit": point.unit}
            )
    if series is not None and series >= len(series_options):
        raise HTTPException(status_code=422, detail="Choose an available measurement series.")
    selected_series = (
        series_options[series if series is not None else 0] if series_options else None
    )
    points = (
        trend_points(
            session,
            user_id=user.id,
            name=str(selected_series["name"]),
            unit=str(selected_series["unit"]),
            days=days,
        )
        if selected_series
        else []
    )
    return render_template(
        request,
        "measurements.html",
        title="Pomiary",
        user=user,
        csrf_token=csrf_token,
        days=days,
        allowed_days=ALLOWED_HISTORY_DAYS,
        records=records,
        owner_measurement_ids={point.id for point in records if point.owner_user_id == user.id},
        series_options=series_options,
        selected_series=selected_series,
        selected_series_index=series if series is not None else (0 if series_options else None),
        chart=chart_data(points),
        datetime_value=_form_datetime_value,
    )


@router.post("/ui", include_in_schema=False)
async def create_measurement_from_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _form_fields(request)
    _verify_form_csrf(user, login_session, request, fields)
    body = _form_measurement_input(fields)
    create_measurement(
        session,
        owner_user_id=user.id,
        name=body.name,
        value=body.value,
        unit=body.unit,
        measured_at=body.measured_at,
        note=body.note,
    )
    return RedirectResponse("/measurements/ui", status_code=303)


@router.post("/{resource_id}/edit", include_in_schema=False)
async def update_measurement_from_form(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_measurement_owner)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _form_fields(request)
    _verify_form_csrf(user, login_session, request, fields)
    body = _form_measurement_input(fields)
    measurement = session.get(Measurement, resource_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found.")
    update_measurement(
        session,
        measurement,
        name=body.name,
        value=body.value,
        unit=body.unit,
        measured_at=body.measured_at,
        note=body.note,
    )
    return RedirectResponse("/measurements/ui", status_code=303)


@router.post("/{resource_id}/delete", include_in_schema=False)
async def delete_measurement_from_form(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_measurement_owner)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _form_fields(request)
    _verify_form_csrf(user, login_session, request, fields)
    measurement = session.get(Measurement, resource_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found.")
    delete_measurement(session, measurement)
    return RedirectResponse("/measurements/ui", status_code=303)


@router.get("/{resource_id}")
def get_measurement(
    resource_id: str,
    _owner_user_id: Annotated[int, Depends(_require_measurement_access)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str | float | None]:
    measurement = session.get(Measurement, resource_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found.")
    return public_measurement(measurement)


@router.put("/{resource_id}")
def replace_measurement(
    resource_id: str,
    body: MeasurementInput,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_measurement_owner)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str | float | None]:
    _require_csrf(user, login_session, request)
    measurement = session.get(Measurement, resource_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found.")
    updated = update_measurement(
        session,
        measurement,
        name=body.name,
        value=body.value,
        unit=body.unit,
        measured_at=body.measured_at,
        note=body.note,
    )
    return public_measurement(updated)


@router.delete("/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_measurement(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_measurement_owner)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    _require_csrf(user, login_session, request)
    measurement = session.get(Measurement, resource_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found.")
    delete_measurement(session, measurement)
