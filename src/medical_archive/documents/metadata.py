import re
from dataclasses import dataclass
from datetime import date

_TYPE_HINTS = (
    ("lab_result", ("wynik", "badanie laboratoryjne", "morfologia", "laboratorium")),
    ("prescription", ("recepta", "receptę", "recepta lekarska")),
    ("discharge_summary", ("wypis", "karta informacyjna leczenia")),
)
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{2})[.-](\d{2})[.-](\d{4})(?!\d)"),
)


@dataclass(frozen=True)
class MetadataSuggestion:
    document_type: str | None
    document_date: date | None


def suggest_metadata(text: str) -> MetadataSuggestion:
    normalized_text = text.casefold()
    document_type = next(
        (
            type_name
            for type_name, hints in _TYPE_HINTS
            if any(hint in normalized_text for hint in hints)
        ),
        None,
    )
    document_date = None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        values = tuple(int(value) for value in match.groups())
        year, month, day = (
            values if pattern is _DATE_PATTERNS[0] else (values[2], values[1], values[0])
        )
        try:
            document_date = date(year, month, day)
        except ValueError:
            continue
        break
    return MetadataSuggestion(document_type, document_date)
