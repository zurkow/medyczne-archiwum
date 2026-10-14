from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import get_authenticated_session, get_current_user
from medical_archive.auth.grants import can_access, visible_resource_ids
from medical_archive.auth.service import rotate_csrf
from medical_archive.db import get_session
from medical_archive.documents.models import Document
from medical_archive.measurements.trends import chart_data, measurement_history, trend_points
from medical_archive.models import AccessGrant, LoginSession, User
from medical_archive.web.templates import render_template

router = APIRouter()

_DASHBOARD_RECENT_LIMIT = 5


def _recent_visible_documents(session: Session, user_id: int) -> list[dict[str, object]]:
    owned_ids = session.scalars(select(Document.id).where(Document.owner_user_id == user_id)).all()
    visible_ids = visible_resource_ids(
        session,
        user_id=user_id,
        resource_type="document",
        owned_resource_ids=owned_ids,
    )
    if not visible_ids:
        return []

    active_grant = exists(
        select(AccessGrant.id).where(
            AccessGrant.owner_user_id == Document.owner_user_id,
            AccessGrant.grantee_user_id == user_id,
            AccessGrant.resource_type == "document",
            AccessGrant.resource_id == Document.id,
        )
    )
    documents = session.scalars(
        select(Document)
        .where(
            Document.id.in_(visible_ids),
            or_(
                Document.owner_user_id == user_id,
                and_(Document.processing_status == "ready", active_grant),
            ),
        )
        .order_by(Document.created_at.desc(), Document.id.asc())
        .limit(_DASHBOARD_RECENT_LIMIT)
    ).all()

    result: list[dict[str, object]] = []
    for document in documents:
        is_owner = document.owner_user_id == user_id
        if not is_owner and (
            document.processing_status != "ready"
            or not can_access(
                session,
                user_id=user_id,
                resource_type="document",
                resource_id=document.id,
                owner_user_id=document.owner_user_id,
            )
        ):
            continue
        result.append(
            {
                "id": document.id,
                "filename": document.original_filename,
                "document_date": document.document_date,
                "url": (
                    f"/documents/{document.id}/review"
                    if is_owner
                    else f"/documents/{document.id}/file"
                ),
            }
        )
    return result


@router.get("/", include_in_schema=False)
def home(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    csrf_token = rotate_csrf(login_session)
    database.commit()
    documents = _recent_visible_documents(database, user.id)
    recent_measurements = measurement_history(
        database,
        user_id=user.id,
        days=30,
        limit=_DASHBOARD_RECENT_LIMIT,
    )
    recent_chart = None
    if recent_measurements:
        latest = recent_measurements[0]
        recent_chart = chart_data(
            trend_points(
                database,
                user_id=user.id,
                name=latest.name,
                unit=latest.unit,
                days=30,
            )
        )
    return render_template(
        request,
        "dashboard.html",
        title="Pulpit pacjenta",
        user=user,
        csrf_token=csrf_token,
        documents=documents,
        recent_measurements=recent_measurements,
        recent_chart=recent_chart,
    )


__all__ = ["router"]
