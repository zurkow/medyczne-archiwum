from datetime import date

from medical_archive.documents.metadata import suggest_metadata


def test_suggests_only_document_type_and_date_from_extracted_text():
    suggestion = suggest_metadata("Wynik badania laboratoryjnego z dnia 12.10.2026")

    assert suggestion.document_type == "lab_result"
    assert suggestion.document_date == date(2026, 10, 12)
    assert not hasattr(suggestion, "owner_user_id")


def test_does_not_suggest_type_or_invalid_date_without_matching_hints():
    suggestion = suggest_metadata("Dokument pacjenta 31.02.2026")

    assert suggestion.document_type is None
    assert suggestion.document_date is None
