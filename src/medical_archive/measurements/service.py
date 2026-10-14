from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from medical_archive.measurements.models import Measurement


def create_measurement(
    session: Session,
    *,
    owner_user_id: int,
    name: str,
    value: Decimal,
    unit: str,
    measured_at: datetime,
    note: str | None,
) -> Measurement:
    measurement = Measurement(
        id=str(uuid4()),
        owner_user_id=owner_user_id,
        name=name,
        value=value,
        unit=unit,
        measured_at_utc=measured_at.astimezone(UTC),
        note=note,
    )
    session.add(measurement)
    session.commit()
    return measurement


def update_measurement(
    session: Session,
    measurement: Measurement,
    *,
    name: str,
    value: Decimal,
    unit: str,
    measured_at: datetime,
    note: str | None,
) -> Measurement:
    measurement.name = name
    measurement.value = value
    measurement.unit = unit
    measurement.measured_at_utc = measured_at.astimezone(UTC)
    measurement.note = note
    session.commit()
    return measurement


def delete_measurement(session: Session, measurement: Measurement) -> None:
    session.delete(measurement)
    session.commit()


def public_measurement(measurement: Measurement) -> dict[str, str | float | None]:
    measured_at = measurement.measured_at_utc
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=UTC)
    else:
        measured_at = measured_at.astimezone(UTC)

    created_at = measurement.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)

    return {
        "id": measurement.id,
        "name": measurement.name,
        "value": float(measurement.value),
        "unit": measurement.unit,
        "measured_at": measured_at.isoformat().replace("+00:00", "Z"),
        "note": measurement.note,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
    }
