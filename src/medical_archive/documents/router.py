from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import (
    get_authenticated_session,
    get_current_user,
    require_resource_access,
)
from medical_archive.auth.grant_router import create_grant_router
from medical_archive.auth.grants import can_access, visible_resource_ids
from medical_archive.auth.service import verify_csrf
from medical_archive.db import get_session
from medical_archive.documents.models import Document
from medical_archive.documents.service import (
    DocumentPersistenceError,
    create_document,
    public_document,
)
from medical_archive.documents.storage import (
    DocumentStorageError,
    UploadError,
    read_multipart_file,
)
from medical_archive.models import LoginSession, User

router = APIRouter(prefix="/documents", tags=["documents"])
_STATIC_DIRECTORY = Path(__file__).resolve().parents[1] / "static"


def _lookup_owner_id(session: Session, document_id: str) -> int | None:
    document = session.get(Document, document_id)
    return document.owner_user_id if document is not None else None


def _lookup_grantable_owner_id(session: Session, document_id: str) -> int | None:
    document = session.get(Document, document_id)
    if document is None or document.processing_status != "ready":
        return None
    return document.owner_user_id


_require_document_access = require_resource_access(
    resource_type="document",
    get_owner_id=_lookup_owner_id,
)
grant_router = create_grant_router(
    resource_type="document",
    get_owner_id=_lookup_grantable_owner_id,
)


@router.post("", status_code=202)
async def upload_document(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str | int | date | datetime | None]:
    if login_session.user_id != user.id or not verify_csrf(
        login_session, request.headers.get("x-csrf-token")
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    try:
        filename, declared_mime, content = await read_multipart_file(request)
        document = create_document(
            session,
            owner_user_id=user.id,
            data_dir=request.app.state.data_dir,
            static_dir=_STATIC_DIRECTORY,
            filename=filename,
            declared_mime=declared_mime,
            content=content,
        )
    except UploadError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except DocumentStorageError as error:
        raise HTTPException(
            status_code=500,
            detail="The document could not be stored.",
        ) from error
    except DocumentPersistenceError as error:
        raise HTTPException(
            status_code=503,
            detail="The document could not be registered. Please try again.",
        ) from error
    return public_document(document)


@router.get("")
def list_documents(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[dict[str, str | int | date | datetime | None]]:
    owned_resource_ids = session.scalars(
        select(Document.id).where(Document.owner_user_id == user.id)
    ).all()
    visible_ids = visible_resource_ids(
        session,
        user_id=user.id,
        resource_type="document",
        owned_resource_ids=owned_resource_ids,
    )
    if not visible_ids:
        return []

    documents = session.scalars(
        select(Document)
        .where(Document.id.in_(visible_ids))
        .order_by(Document.created_at.desc(), Document.id)
    ).all()
    visible_documents = [
        document
        for document in documents
        if document.owner_user_id == user.id
        or (
            document.processing_status == "ready"
            and can_access(
                session,
                user_id=user.id,
                resource_type="document",
                resource_id=document.id,
                owner_user_id=document.owner_user_id,
            )
        )
    ]
    return [public_document(document) for document in visible_documents]


@router.get("/{resource_id}")
def get_document(
    resource_id: str,
    user: Annotated[User, Depends(get_current_user)],
    owner_user_id: Annotated[int, Depends(_require_document_access)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str | int | date | datetime | None]:
    document = session.get(Document, resource_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if owner_user_id != user.id and document.processing_status != "ready":
        raise HTTPException(status_code=404, detail="Document not found.")
    return public_document(document)


@router.get("/{resource_id}/file")
def download_document(
    resource_id: str,
    user: Annotated[User, Depends(get_current_user)],
    owner_user_id: Annotated[int, Depends(_require_document_access)],
    session: Annotated[Session, Depends(get_session)],
    request: Request,
) -> FileResponse:
    document = session.get(Document, resource_id)
    if document is None or (owner_user_id != user.id and document.processing_status != "ready"):
        raise HTTPException(status_code=404, detail="Document not found.")
    file_path = request.app.state.data_dir / "documents" / document.storage_key
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")
    return FileResponse(
        file_path,
        media_type=document.mime_type,
        filename=document.original_filename,
    )
