import logging
from collections.abc import Sequence
from datetime import date
from time import perf_counter
from typing import Protocol

from sqlalchemy.orm import Session

from medical_archive.documents.extraction import ExtractedPage
from medical_archive.documents.models import Document

logger = logging.getLogger(__name__)

ALLOWED_DOCUMENT_TYPES = {
    "lab_result",
    "prescription",
    "discharge_summary",
    "imaging",
    "vaccination",
    "other",
}


class DocumentIndexer(Protocol):
    def index_document(self, document: Document, pages: Sequence[ExtractedPage]) -> None: ...


class UnavailableDocumentIndexer:
    def index_document(self, document: Document, pages: Sequence[ExtractedPage]) -> None:
        raise RuntimeError("Document indexing is not configured.")


class DocumentReviewError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def approve_document(
    session: Session,
    document: Document,
    *,
    document_type: str | None,
    document_date: date | None,
    indexer: DocumentIndexer,
) -> Document:
    if document.processing_status not in {"needs_review", "index_failed"}:
        raise DocumentReviewError(
            "invalid_state", "This document is not waiting for approval.", 409
        )
    if document_type is not None and document_type not in ALLOWED_DOCUMENT_TYPES:
        raise DocumentReviewError("invalid_type", "Choose a valid document type.", 422)
    if not document.pages:
        raise DocumentReviewError(
            "text_missing", "Process the document before approving its metadata.", 409
        )

    document.document_type = document_type
    document.document_date = document_date
    document.processing_status = "indexing"
    session.commit()

    started = perf_counter()
    try:
        indexer.index_document(document, list(document.pages))
    except Exception as error:
        document.processing_status = "index_failed"
        session.commit()
        logger.warning(
            "document_indexing_failed document_id=%s code=%s duration_ms=%d",
            document.id,
            "index_failed",
            round((perf_counter() - started) * 1000),
        )
        raise DocumentReviewError(
            "index_failed",
            "The document could not be added to the search index. You can retry.",
            503,
        ) from error

    document.processing_status = "ready"
    session.commit()
    logger.info(
        "document_indexing_completed document_id=%s code=%s duration_ms=%d",
        document.id,
        "ok",
        round((perf_counter() - started) * 1000),
    )
    return document
