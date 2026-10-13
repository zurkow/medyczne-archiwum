import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ExtractionError(Exception):
    """A safe, user-facing document extraction failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    method: str


class OcrAdapter(Protocol):
    def recognize(self, image: bytes, *, lang: str) -> str: ...


class TesseractOcr:
    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self.timeout_seconds = timeout_seconds

    def recognize(self, image: bytes, *, lang: str) -> str:
        executable = shutil.which("tesseract")
        if executable is None:
            raise ExtractionError(
                "tesseract_missing",
                "Tesseract OCR is not installed. Install Tesseract with Polish language data.",
            )
        try:
            languages = subprocess.run(
                [executable, "--list-langs"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExtractionError(
                "tesseract_unavailable", "Tesseract OCR could not be started."
            ) from error
        if languages.returncode != 0:
            raise ExtractionError("tesseract_unavailable", "Tesseract OCR could not be configured.")
        if lang not in languages.stdout.splitlines():
            raise ExtractionError(
                "pol_traineddata_missing",
                "Tesseract Polish language data (pol.traineddata) is not installed.",
            )
        try:
            result = subprocess.run(
                [executable, "stdin", "stdout", "-l", lang],
                input=image,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ExtractionError(
                "ocr_timeout", "OCR took too long to process this page."
            ) from error
        except OSError as error:
            raise ExtractionError(
                "tesseract_unavailable", "Tesseract OCR could not run."
            ) from error
        if result.returncode != 0:
            raise ExtractionError("ocr_failed", "OCR could not read this page.")
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise ExtractionError("ocr_failed", "OCR returned unreadable text.") from error


class TextExtractor:
    def __init__(self, *, ocr: OcrAdapter | None = None) -> None:
        self.ocr = ocr or TesseractOcr()

    def extract(self, path: Path) -> list[ExtractedPage]:
        try:
            import pymupdf
        except ImportError as error:
            raise ExtractionError(
                "pymupdf_missing", "PDF and image extraction requires the PyMuPDF package."
            ) from error

        if not path.is_file():
            raise ExtractionError("unreadable_input", "The original document could not be read.")

        try:
            with pymupdf.open(path) as document:
                if document.page_count == 0:
                    raise ExtractionError(
                        "unreadable_input", "The document does not contain readable pages."
                    )
                extracted: list[ExtractedPage] = []
                for page_number, page in enumerate(document, start=1):
                    text = page.get_text("text").strip() if path.suffix.lower() == ".pdf" else ""
                    if text:
                        extracted.append(ExtractedPage(page_number, text, "text"))
                        continue
                    try:
                        image = page.get_pixmap(dpi=200, alpha=False).tobytes("png")
                    except Exception as error:
                        raise ExtractionError(
                            "unreadable_input", "A document page could not be rendered."
                        ) from error
                    text = self.ocr.recognize(image, lang="pol").strip()
                    extracted.append(ExtractedPage(page_number, text, "ocr"))
        except ExtractionError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise ExtractionError(
                "unreadable_input", "The original document could not be read."
            ) from error

        if not any(page.text for page in extracted):
            raise ExtractionError("empty_output", "No readable text was found in the document.")
        return extracted
