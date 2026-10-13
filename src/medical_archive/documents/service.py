from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from medical_archive.documents.models import Document
from medical_archive.documents.storage import (
    validate_upload,
    write_document,
)


class DocumentPersistenceError(Exception):
    """Raised when a document record cannot be committed."""


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
        "created_at": document.created_at,
    }
