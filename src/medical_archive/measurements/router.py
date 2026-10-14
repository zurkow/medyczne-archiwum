from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
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
from medical_archive.auth.service import verify_csrf
from medical_archive.db import get_session
from medical_archive.measurements.models import Measurement
from medical_archive.measurements.service import (
    create_measurement,
    delete_measurement,
    public_measurement,
    update_measurement,
)
from medical_archive.models import LoginSession, User

router = APIRouter(prefix="/measurements", tags=["measurements"])


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
