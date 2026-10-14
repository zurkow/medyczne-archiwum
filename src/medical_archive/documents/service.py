import logging
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from medical_archive.config import Settings
from medical_archive.documents.extraction import ExtractionError
from medical_archive.documents.metadata import MetadataSuggestion, suggest_metadata
from medical_archive.documents.models import Document, DocumentPage
from medical_archive.documents.storage import (
    validate_upload,
    write_document,
)
from medical_archive.rag.embeddings import EmbeddingClient
from medical_archive.rag.index import DocumentVectorIndexer
from medical_archive.rag.models import EMBEDDING_DIMENSIONS
from medical_archive.rag.retrieval import DocumentRetriever
from medical_archive.rag.service import AnswerService, LMStudioClient

logger = logging.getLogger(__name__)


class DocumentPersistenceError(Exception):
    """Raised when a document record cannot be committed."""


class DocumentProcessingError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def create_document_indexer(session: Session) -> DocumentVectorIndexer:
    settings = Settings(testing=True)
    embedder = EmbeddingClient(
        model=settings.embedding_model,
        dimensions=EMBEDDING_DIMENSIONS,
        base_url=settings.lm_studio_base_url,
        timeout=settings.embedding_timeout_seconds,
    )
    return DocumentVectorIndexer(session, embedder=embedder)


def create_answer_service(session: Session) -> AnswerService:
    settings = Settings(testing=True)
    embedder = EmbeddingClient(
        model=settings.embedding_model,
        dimensions=EMBEDDING_DIMENSIONS,
        base_url=settings.lm_studio_base_url,
        timeout=settings.embedding_timeout_seconds,
    )
    retriever = DocumentRetriever(
        session,
        embedder=embedder,
        relevance_threshold=settings.retrieval_relevance_threshold,
        top_k=settings.retrieval_top_k,
    )
    generator = LMStudioClient(
        base_url=settings.lm_studio_base_url,
        model=settings.chat_model,
        timeout=settings.generation_timeout_seconds,
    )
    return AnswerService(retriever=retriever, generator=generator)


def create_document(
    session: Session,
    *,
    owner_user_id: int,
    data_dir: Path,
    static_dir: Path,
    filename: str,
    declared_mime: str,
    content: bytes,
) -> Document:
    safe_filename, mime_type = validate_upload(filename, declared_mime, content)
    document_id = str(uuid4())
    storage_key = str(uuid4())
    stored_path = write_document(
        data_dir,
        static_dir=static_dir,
        storage_key=storage_key,
        content=content,
    )
    document = Document(
        id=document_id,
        owner_user_id=owner_user_id,
        storage_key=storage_key,
        original_filename=safe_filename,
        mime_type=mime_type,
        byte_size=len(content),
        processing_status="needs_review",
    )
    try:
        session.add(document)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        try:
            stored_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise DocumentPersistenceError from error
    return document


def public_document(document: Document) -> dict[str, str | int | date | datetime | None]:
    return {
        "id": document.id,
        "filename": document.original_filename,
        "mime_type": document.mime_type,
        "byte_size": document.byte_size,
        "document_type": document.document_type,
        "document_date": document.document_date,
        "status": document.processing_status,
        "ocr_status": document.ocr_status,
        "created_at": document.created_at,
    }


def process_document(
    session: Session,
    document: Document,
    *,
    data_dir: Path,
    extractor,
    retry: bool = False,
) -> tuple[Document, MetadataSuggestion]:
    expected_status = "ocr_failed" if retry else "needs_review"
    if document.processing_status != expected_status:
        raise DocumentProcessingError(
            "invalid_state",
            "This document is not available for text extraction in its current state.",
            409,
        )

    started = perf_counter()
    path = data_dir / "documents" / document.storage_key
    try:
        pages = extractor.extract(path)
    except ExtractionError as error:
        document.processing_status = "ocr_failed"
        document.ocr_status = "failed"
        session.commit()
        logger.warning(
            "document_extraction_failed document_id=%s code=%s duration_ms=%d",
            document.id,
            error.code,
            round((perf_counter() - started) * 1000),
        )
        raise DocumentProcessingError(error.code, error.message) from error
    except Exception as error:
        document.processing_status = "ocr_failed"
        document.ocr_status = "failed"
        session.commit()
        logger.warning(
            "document_extraction_failed document_id=%s code=%s duration_ms=%d",
            document.id,
            "extraction_failed",
            round((perf_counter() - started) * 1000),
        )
        raise DocumentProcessingError(
            "extraction_failed", "The document could not be processed."
        ) from error

    if not pages or not any(page.text.strip() for page in pages):
        document.processing_status = "ocr_failed"
        document.ocr_status = "failed"
        session.commit()
        logger.warning(
            "document_extraction_failed document_id=%s code=%s duration_ms=%d",
            document.id,
            "empty_output",
            round((perf_counter() - started) * 1000),
        )
        raise DocumentProcessingError("empty_output", "No readable text was found in the document.")

    document.pages.clear()
    document.pages.extend(
        DocumentPage(
            page_number=page.page_number,
            text=page.text,
            extraction_method=page.method,
        )
        for page in pages
    )
    document.ocr_status = "completed" if any(page.method == "ocr" for page in pages) else "text"
    document.processing_status = "needs_review"
    session.commit()
    suggestion = suggest_metadata("\n".join(page.text for page in pages))
    logger.info(
        "document_extraction_completed document_id=%s code=%s duration_ms=%d",
        document.id,
        "ok",
        round((perf_counter() - started) * 1000),
    )
    return document, suggestion
