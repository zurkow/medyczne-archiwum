import pymupdf as fitz
import pytest

from medical_archive.documents.extraction import (
    ExtractedPage,
    ExtractionError,
    TesseractOcr,
    TextExtractor,
)


class FakeOcr:
    def __init__(self, text: str = "tekst odczytany") -> None:
        self.text = text
        self.languages: list[str] = []

    def recognize(self, image: bytes, *, lang: str) -> str:
        assert image
        self.languages.append(lang)
        return self.text


def test_pdf_text_layer_is_extracted_page_by_page(tmp_path):
    pdf_path = tmp_path / "report.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Wynik badania")
    pdf.save(pdf_path)
    pdf.close()

    pages = TextExtractor().extract(pdf_path)

    assert pages == [ExtractedPage(page_number=1, text="Wynik badania", method="text")]


def test_image_is_processed_with_polish_ocr(tmp_path):
    image_path = tmp_path / "scan.png"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
    pixmap.save(image_path)
    ocr = FakeOcr()

    pages = TextExtractor(ocr=ocr).extract(image_path)

    assert pages == [ExtractedPage(page_number=1, text="tekst odczytany", method="ocr")]
    assert ocr.languages == ["pol"]


def test_missing_tesseract_has_a_specific_extraction_error(monkeypatch):
    monkeypatch.setattr("medical_archive.documents.extraction.shutil.which", lambda _name: None)

    with pytest.raises(ExtractionError) as error:
        TesseractOcr().recognize(b"image", lang="pol")

    assert error.value.code == "tesseract_missing"


def test_missing_polish_language_data_has_a_specific_error(monkeypatch):
    class LanguageList:
        returncode = 0
        stdout = "List of available languages in tessdata directory:\neng\n"

    monkeypatch.setattr(
        "medical_archive.documents.extraction.shutil.which", lambda _name: "tesseract.exe"
    )
    monkeypatch.setattr(
        "medical_archive.documents.extraction.subprocess.run",
        lambda *_args, **_kwargs: LanguageList(),
    )

    with pytest.raises(ExtractionError) as error:
        TesseractOcr().recognize(b"image", lang="pol")

    assert error.value.code == "pol_traineddata_missing"


def test_empty_ocr_output_is_not_reported_as_success(tmp_path):
    image_path = tmp_path / "blank.png"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
    pixmap.save(image_path)

    with pytest.raises(ExtractionError) as error:
        TextExtractor(ocr=FakeOcr(" ")).extract(image_path)

    assert error.value.code == "empty_output"


def test_unreadable_original_is_reported_explicitly(tmp_path):
    with pytest.raises(ExtractionError) as error:
        TextExtractor().extract(tmp_path / "missing.pdf")

    assert error.value.code == "unreadable_input"
