from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from medical_archive.auth.grants import can_access, visible_resource_ids
from medical_archive.measurements.models import Measurement
from medical_archive.models import AccessGrant

ALLOWED_HISTORY_DAYS = (7, 30, 90, 365)
MAX_TREND_POINTS = 200
MAX_HISTORY_RECORDS = 100


@dataclass(frozen=True)
class TrendPoint:
    id: str
    owner_user_id: int
    name: str
    value: Decimal
    unit: str
    measured_at: datetime
    note: str | None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _visible_measurement_query(
    session: Session,
    *,
    user_id: int,
    days: int,
):
    if days not in ALLOWED_HISTORY_DAYS:
        raise ValueError("History range must be 7, 30, 90, or 365 days.")

    owned_ids = session.scalars(
        select(Measurement.id).where(Measurement.owner_user_id == user_id)
    ).all()
    visible_ids = visible_resource_ids(
        session,
        user_id=user_id,
        resource_type="measurement",
        owned_resource_ids=owned_ids,
    )
    if not visible_ids:
        return None

    has_active_grant = exists(
        select(AccessGrant.id).where(
            AccessGrant.owner_user_id == Measurement.owner_user_id,
            AccessGrant.grantee_user_id == user_id,
            AccessGrant.resource_type == "measurement",
            AccessGrant.resource_id == Measurement.id,
        )
    )
    access_filter = or_(Measurement.owner_user_id == user_id, has_active_grant)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return select(Measurement).where(
        Measurement.id.in_(visible_ids),
        access_filter,
        Measurement.measured_at_utc >= cutoff,
    )


def _to_points(measurements: Sequence[Measurement]) -> list[TrendPoint]:
    return [
        TrendPoint(
            id=measurement.id,
            owner_user_id=measurement.owner_user_id,
            name=measurement.name,
            value=Decimal(measurement.value),
            unit=measurement.unit,
            measured_at=_as_utc(measurement.measured_at_utc),
            note=measurement.note,
        )
        for measurement in measurements
    ]


def measurement_history(
    session: Session,
    *,
    user_id: int,
    days: int,
    limit: int = MAX_HISTORY_RECORDS,
) -> list[TrendPoint]:
    statement = _visible_measurement_query(session, user_id=user_id, days=days)
    if statement is None:
        return []
    measurements = session.scalars(
        statement.order_by(Measurement.measured_at_utc.desc(), Measurement.id.asc()).limit(
            min(limit, MAX_HISTORY_RECORDS)
        )
    ).all()
    return _to_points(
        [
            measurement
            for measurement in measurements
            if measurement.owner_user_id == user_id
            or can_access(
                session,
                user_id=user_id,
                resource_type="measurement",
                resource_id=measurement.id,
                owner_user_id=measurement.owner_user_id,
            )
        ]
    )


def trend_points(
    session: Session,
    *,
    user_id: int,
    name: str,
    unit: str,
    days: int,
    limit: int = MAX_TREND_POINTS,
) -> list[TrendPoint]:
    statement = _visible_measurement_query(session, user_id=user_id, days=days)
    if statement is None:
        return []
    statement = statement.where(Measurement.name == name, Measurement.unit == unit)
    measurements = session.scalars(
        statement.order_by(Measurement.measured_at_utc.asc(), Measurement.id.asc()).limit(
            min(limit, MAX_TREND_POINTS)
        )
    ).all()
    return _to_points(
        [
            measurement
            for measurement in measurements
            if measurement.owner_user_id == user_id
            or can_access(
                session,
                user_id=user_id,
                resource_type="measurement",
                resource_id=measurement.id,
                owner_user_id=measurement.owner_user_id,
            )
        ]
    )


def chart_data(points: Sequence[TrendPoint]) -> dict[str, object] | None:
    if not points:
        return None

    width = 640
    height = 300
    left = 76
    right = 620
    top = 24
    bottom = 238
    values = [point.value for point in points]
    minimum = min(values)
    maximum = max(values)
    value_span = maximum - minimum
    times = [point.measured_at.timestamp() for point in points]
    time_span = max(times) - min(times)
    plotted = []
    for point, timestamp in zip(points, times, strict=True):
        x = (
            (left + right) / 2
            if time_span == 0
            else left + ((timestamp - min(times)) / time_span) * (right - left)
        )
        y = (
            (top + bottom) / 2
            if value_span == 0
            else bottom - (float(point.value - minimum) / float(value_span)) * (bottom - top)
        )
        plotted.append(
            {
                "id": point.id,
                "x": round(x, 2),
                "y": round(y, 2),
                "value": str(point.value),
                "unit": point.unit,
                "measured_at": point.measured_at,
                "label": point.measured_at.strftime("%Y-%m-%d %H:%M UTC"),
            }
        )
    return {
        "width": width,
        "height": height,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "min_label": f"{minimum} {points[0].unit}",
        "max_label": f"{maximum} {points[0].unit}",
        "first_label": points[0].measured_at.strftime("%Y-%m-%d"),
        "last_label": points[-1].measured_at.strftime("%Y-%m-%d"),
        "points": plotted,
        "name": points[0].name,
        "unit": points[0].unit,
    }
