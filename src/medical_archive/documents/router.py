from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import (
    get_authenticated_session,
    get_current_user,
    require_resource_access,
    require_resource_owner,
)
from medical_archive.auth.grant_router import create_grant_router
from medical_archive.auth.grants import can_access, visible_resource_ids
from medical_archive.auth.service import rotate_csrf, verify_csrf
from medical_archive.db import get_session
from medical_archive.documents.extraction import TextExtractor
from medical_archive.documents.metadata import MetadataSuggestion, suggest_metadata
from medical_archive.documents.models import Document
from medical_archive.documents.review import (
    ALLOWED_DOCUMENT_TYPES,
    DocumentReviewError,
    approve_document,
)
from medical_archive.documents.service import (
    DocumentPersistenceError,
    DocumentProcessingError,
    create_document,
    create_document_indexer,
    public_document,
)
from medical_archive.documents.service import (
    process_document as extract_document_text,
)
from medical_archive.documents.storage import (
    DocumentStorageError,
    UploadError,
    read_multipart_file,
)
from medical_archive.models import AccessGrant, LoginSession, User
from medical_archive.web.templates import render_template

router = APIRouter(prefix="/documents", tags=["documents"])
_STATIC_DIRECTORY = Path(__file__).resolve().parents[1] / "static"
_MAX_FORM_BYTES = 16_384


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
_require_document_owner = require_resource_owner(
    resource_type="document",
    get_owner_id=_lookup_owner_id,
)


async def _request_fields(request: Request) -> dict[str, object]:
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0]
    if content_type == "application/json":
        try:
            fields = await request.json()
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid request data.") from error
        if not isinstance(fields, dict):
            raise HTTPException(status_code=400, detail="Invalid request data.")
        return fields
    if content_type == "application/x-www-form-urlencoded":
        body = await request.body()
        if len(body) > _MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail="Form data is too large.")
        try:
            parsed = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=16,
                strict_parsing=False,
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="Invalid form data.") from error
        if any(len(values) != 1 for values in parsed.values()):
            raise HTTPException(status_code=400, detail="Duplicate form fields are not allowed.")
        return {key: values[0] for key, values in parsed.items()}
    return {}


def _verify_owner_action_csrf(
    user: User,
    login_session: LoginSession,
    request: Request,
    fields: dict[str, object],
) -> None:
    csrf_token = request.headers.get("x-csrf-token") or fields.get("csrf_token")
    if (
        login_session.user_id != user.id
        or not isinstance(csrf_token, str)
        or not verify_csrf(login_session, csrf_token)
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


def _review_payload(document: Document, suggestion: MetadataSuggestion) -> dict[str, object]:
    payload: dict[str, object] = public_document(document)
    payload.update(
        {
            "suggested_document_type": suggestion.document_type,
            "suggested_document_date": (
                suggestion.document_date.isoformat() if suggestion.document_date else None
            ),
            "pages": [
                {
                    "page_number": page.page_number,
                    "text": page.text,
                    "method": page.extraction_method,
                }
                for page in document.pages
            ],
        }
    )
    return payload


def _suggestion_for_document(document: Document) -> MetadataSuggestion:
    return suggest_metadata("\n".join(page.text for page in document.pages))


def _indexer_for_request(request: Request, session: Session):
    configured_indexer = getattr(request.app.state, "document_indexer", None)
    if configured_indexer is not None:
        return configured_indexer
    return create_document_indexer(session)


def _apply_document_grant(request: Request, session: Session, grant: AccessGrant) -> None:
    document = session.get(Document, grant.resource_id)
    indexer = _indexer_for_request(request, session)
    apply_grant = getattr(indexer, "apply_grant", None)
    if document is not None and apply_grant is not None:
        apply_grant(document, grant)


def _remove_document_grant(request: Request, session: Session, grant: AccessGrant) -> None:
    indexer = _indexer_for_request(request, session)
    remove_from_partition = getattr(indexer, "remove_from_partition", None)
    if remove_from_partition is not None:
        remove_from_partition(
            owner_id=grant.grantee_user_id,
            document_id=grant.resource_id,
        )


grant_router = create_grant_router(
    resource_type="document",
    get_owner_id=_lookup_grantable_owner_id,
    on_grant=_apply_document_grant,
    on_revoke=_remove_document_grant,
)


def _extractor_for_request(request: Request):
    return getattr(request.app.state, "document_extractor", TextExtractor())


def _workflow_error(error: DocumentProcessingError | DocumentReviewError, status: str):
    return JSONResponse(
        status_code=error.status_code,
        content={"status": status, "error": error.code, "detail": error.message},
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


@router.get("/ui", include_in_schema=False)
def document_list_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    csrf_token = rotate_csrf(login_session)
    session.commit()
    documents = session.scalars(
        select(Document)
        .where(Document.owner_user_id == user.id)
        .order_by(Document.created_at.desc(), Document.id)
    ).all()
    return render_template(
        request,
        "documents.html",
        title="Dokumenty",
        user=user,
        csrf_token=csrf_token,
        documents=[public_document(document) for document in documents],
    )


@router.get("/{resource_id}/review", include_in_schema=False)
def document_review_page(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_document_owner)],
    session: Annotated[Session, Depends(get_session)],
):
    document = session.get(Document, resource_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    csrf_token = rotate_csrf(login_session)
    session.commit()
    return render_template(
        request,
        "document_review.html",
        title="Sprawdź dokument",
        user=user,
        csrf_token=csrf_token,
        document=document,
        suggestion=_suggestion_for_document(document),
        document_types=sorted(ALLOWED_DOCUMENT_TYPES),
    )


@router.post("/{resource_id}/process")
async def process_uploaded_document(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_document_owner)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _request_fields(request)
    _verify_owner_action_csrf(user, login_session, request, fields)
    document = session.get(Document, resource_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        document, suggestion = extract_document_text(
            session,
            document,
            data_dir=request.app.state.data_dir,
            extractor=_extractor_for_request(request),
        )
    except DocumentProcessingError as error:
        return _workflow_error(error, document.processing_status)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(f"/documents/{resource_id}/review", status_code=303)
    return _review_payload(document, suggestion)


@router.post("/{resource_id}/retry")
async def retry_document_processing(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_document_owner)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _request_fields(request)
    _verify_owner_action_csrf(user, login_session, request, fields)
    document = session.get(Document, resource_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.processing_status == "index_failed":
        try:
            document = approve_document(
                session,
                document,
                document_type=document.document_type,
                document_date=document.document_date,
                indexer=_indexer_for_request(request, session),
            )
        except DocumentReviewError as error:
            return _workflow_error(error, document.processing_status)
        return public_document(document)
    try:
        document, suggestion = extract_document_text(
            session,
            document,
            data_dir=request.app.state.data_dir,
            extractor=_extractor_for_request(request),
            retry=True,
        )
    except DocumentProcessingError as error:
        return _workflow_error(error, document.processing_status)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(f"/documents/{resource_id}/review", status_code=303)
    return _review_payload(document, suggestion)


@router.post("/{resource_id}/approve")
async def approve_document_metadata(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    _owner_user_id: Annotated[int, Depends(_require_document_owner)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _request_fields(request)
    _verify_owner_action_csrf(user, login_session, request, fields)
    document = session.get(Document, resource_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    raw_type = fields.get("document_type")
    if raw_type is not None and not isinstance(raw_type, str):
        raise HTTPException(status_code=422, detail="Choose a valid document type.")
    document_type = raw_type if isinstance(raw_type, str) and raw_type else None
    raw_date = fields.get("document_date")
    if raw_date is not None and not isinstance(raw_date, str):
        raise HTTPException(status_code=422, detail="Enter a valid document date.")
    try:
        document_date = (
            date.fromisoformat(raw_date) if isinstance(raw_date, str) and raw_date else None
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Enter a valid document date.") from None
    try:
        document = approve_document(
            session,
            document,
            document_type=document_type,
            document_date=document_date,
            indexer=_indexer_for_request(request, session),
        )
    except DocumentReviewError as error:
        return _workflow_error(error, document.processing_status)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(f"/documents/{resource_id}/review", status_code=303)
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


@router.get("/{resource_id}/preview", include_in_schema=False)
def preview_document(
    resource_id: str,
    owner_user_id: Annotated[int, Depends(_require_document_access)],
    session: Annotated[Session, Depends(get_session)],
    request: Request,
) -> FileResponse:
    document = session.get(Document, resource_id)
    if document is None or (
        owner_user_id != document.owner_user_id and document.processing_status != "ready"
    ):
        raise HTTPException(status_code=404, detail="Document not found.")
    file_path = request.app.state.data_dir / "documents" / document.storage_key
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")
    return FileResponse(
        file_path,
        media_type=document.mime_type,
        filename=document.original_filename,
        content_disposition_type="inline",
    )
