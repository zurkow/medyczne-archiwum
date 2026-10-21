from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.weight.models import UserHeight, WeightEntry

MAX_ENTRIES = 200
MIN_WEIGHT_KG = Decimal(20)
MAX_WEIGHT_KG = Decimal(500)
MIN_HEIGHT_CM = Decimal(50)
MAX_HEIGHT_CM = Decimal(250)
UNDERWEIGHT_BELOW = Decimal("18.5")
OVERWEIGHT_FROM = Decimal(25)
_ONE_DECIMAL = Decimal("0.1")

_CHART_WIDTH = 640
_CHART_HEIGHT = 300
_CHART_LEFT = 76
_CHART_RIGHT = 620
_CHART_TOP = 24
_CHART_BOTTOM = 238
_AXIS_MINIMUM = Decimal(15)
_AXIS_MAXIMUM = Decimal(40)


class WeightError(ValueError):
    """A submitted weight, height or date is not acceptable."""


@dataclass(frozen=True)
class BmiCategory:
    key: str
    label: str


UNDERWEIGHT = BmiCategory("underweight", "Niedowaga")
NORMAL = BmiCategory("normal", "BMI prawidłowe")
OVERWEIGHT = BmiCategory("overweight", "Nadwaga")


@dataclass(frozen=True)
class WeightRow:
    id: str
    measured_on: date
    weight_kg: Decimal
    bmi: Decimal | None
    category: BmiCategory | None


def _parse_bounded_decimal(
    raw: str, *, label: str, minimum: Decimal, maximum: Decimal, unit: str
) -> Decimal:
    try:
        value = Decimal(raw.strip().replace(",", "."))
    except InvalidOperation as error:
        raise WeightError(f"{label}: podaj liczbę.") from error
    if not value.is_finite():
        raise WeightError(f"{label}: podaj liczbę.")
    if max(-value.as_tuple().exponent, 0) > 1:
        raise WeightError(f"{label}: podaj najwyżej jedną cyfrę po przecinku.")
    if not minimum <= value <= maximum:
        raise WeightError(f"{label}: podaj wartość od {minimum} do {maximum} {unit}.")
    return value


def parse_weight_kg(raw: str) -> Decimal:
    return _parse_bounded_decimal(
        raw, label="Waga", minimum=MIN_WEIGHT_KG, maximum=MAX_WEIGHT_KG, unit="kg"
    )


def parse_height_cm(raw: str) -> Decimal:
    return _parse_bounded_decimal(
        raw, label="Wzrost", minimum=MIN_HEIGHT_CM, maximum=MAX_HEIGHT_CM, unit="cm"
    )


def parse_measured_on(raw: str, *, today: date) -> date:
    try:
        value = date.fromisoformat(raw.strip())
    except ValueError as error:
        raise WeightError("Data: podaj prawidłową datę.") from error
    if value > today:
        raise WeightError("Data: nie może być z przyszłości.")
    return value


def today_utc(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def calculate_bmi(weight_kg: Decimal, height_cm: Decimal) -> Decimal:
    height_m = height_cm / 100
    return (weight_kg / (height_m * height_m)).quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)


def bmi_category(bmi: Decimal) -> BmiCategory:
    if bmi < UNDERWEIGHT_BELOW:
        return UNDERWEIGHT
    if bmi < OVERWEIGHT_FROM:
        return NORMAL
    return OVERWEIGHT


def get_height_cm(session: Session, *, owner_user_id: int) -> Decimal | None:
    height = session.get(UserHeight, owner_user_id)
    return Decimal(height.height_cm).normalize() if height is not None else None


def set_height_cm(session: Session, *, owner_user_id: int, height_cm: Decimal) -> None:
    height = session.get(UserHeight, owner_user_id)
    if height is None:
        session.add(UserHeight(owner_user_id=owner_user_id, height_cm=height_cm))
    else:
        height.height_cm = height_cm
    session.commit()


def add_entry(
    session: Session, *, owner_user_id: int, weight_kg: Decimal, measured_on: date
) -> WeightEntry:
    entry = WeightEntry(
        id=str(uuid4()),
        owner_user_id=owner_user_id,
        weight_kg=weight_kg,
        measured_on=measured_on,
    )
    session.add(entry)
    session.commit()
    return entry


def delete_entry(session: Session, *, owner_user_id: int, entry_id: str) -> bool:
    entry = session.get(WeightEntry, entry_id)
    if entry is None or entry.owner_user_id != owner_user_id:
        return False
    session.delete(entry)
    session.commit()
    return True


def list_rows(session: Session, *, owner_user_id: int) -> list[WeightRow]:
    """Return the newest entries of the owner in chronological order."""
    newest = session.scalars(
        select(WeightEntry)
        .where(WeightEntry.owner_user_id == owner_user_id)
        .order_by(WeightEntry.measured_on.desc(), WeightEntry.id.asc())
        .limit(MAX_ENTRIES)
    ).all()
    height_cm = get_height_cm(session, owner_user_id=owner_user_id)
    rows = []
    for entry in reversed(newest):
        weight_kg = Decimal(entry.weight_kg).normalize()
        bmi = calculate_bmi(weight_kg, height_cm) if height_cm is not None else None
        rows.append(
            WeightRow(
                id=entry.id,
                measured_on=entry.measured_on,
                weight_kg=weight_kg,
                bmi=bmi,
                category=bmi_category(bmi) if bmi is not None else None,
            )
        )
    return rows


def _bmi_y(bmi: Decimal) -> float:
    clamped = min(max(bmi, _AXIS_MINIMUM), _AXIS_MAXIMUM)
    share = float((clamped - _AXIS_MINIMUM) / (_AXIS_MAXIMUM - _AXIS_MINIMUM))
    return round(_CHART_BOTTOM - share * (_CHART_BOTTOM - _CHART_TOP), 2)


def bmi_chart(rows: Sequence[WeightRow]) -> dict[str, object] | None:
    """Build the BMI chart geometry with underweight, normal and overweight bands."""
    points_source = [row for row in rows if row.bmi is not None]
    if not points_source:
        return None

    first_day = points_source[0].measured_on.toordinal()
    day_span = points_source[-1].measured_on.toordinal() - first_day
    points = []
    for row in points_source:
        x = (
            (_CHART_LEFT + _CHART_RIGHT) / 2
            if day_span == 0
            else _CHART_LEFT
            + (row.measured_on.toordinal() - first_day) / day_span * (_CHART_RIGHT - _CHART_LEFT)
        )
        points.append(
            {
                "x": round(x, 2),
                "y": _bmi_y(row.bmi),
                "bmi": row.bmi,
                "category": row.category,
                "label": row.measured_on.isoformat(),
            }
        )

    normal_top = _bmi_y(OVERWEIGHT_FROM)
    normal_bottom = _bmi_y(UNDERWEIGHT_BELOW)
    bands = [
        {
            "category": OVERWEIGHT,
            "range": f"BMI od {OVERWEIGHT_FROM}",
            "y": _CHART_TOP,
            "height": round(normal_top - _CHART_TOP, 2),
        },
        {
            "category": NORMAL,
            "range": f"BMI {UNDERWEIGHT_BELOW}–24,9",
            "y": normal_top,
            "height": round(normal_bottom - normal_top, 2),
        },
        {
            "category": UNDERWEIGHT,
            "range": f"BMI poniżej {UNDERWEIGHT_BELOW}",
            "y": normal_bottom,
            "height": round(_CHART_BOTTOM - normal_bottom, 2),
        },
    ]
    return {
        "width": _CHART_WIDTH,
        "height": _CHART_HEIGHT,
        "left": _CHART_LEFT,
        "right": _CHART_RIGHT,
        "top": _CHART_TOP,
        "bottom": _CHART_BOTTOM,
        "bands": bands,
        "points": points,
        "first_label": points_source[0].measured_on.isoformat(),
        "last_label": points_source[-1].measured_on.isoformat(),
        "axis_minimum": _AXIS_MINIMUM,
        "axis_maximum": _AXIS_MAXIMUM,
    }
