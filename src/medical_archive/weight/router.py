from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import get_authenticated_session, get_current_user
from medical_archive.auth.service import rotate_csrf, verify_csrf
from medical_archive.db import get_session
from medical_archive.models import LoginSession, User
from medical_archive.web.forms import read_form_fields
from medical_archive.web.templates import render_template
from medical_archive.weight.service import (
    MAX_ENTRIES,
    WeightError,
    add_entry,
    bmi_chart,
    delete_entry,
    get_height_cm,
    list_rows,
    parse_height_cm,
    parse_measured_on,
    parse_weight_kg,
    set_height_cm,
    today_utc,
)

router = APIRouter(prefix="/weight", tags=["weight"])
_MAX_FORM_BYTES = 4096
_MAX_FORM_FIELDS = 4


def _render_page(
    request: Request,
    *,
    user: User,
    login_session: LoginSession,
    session: Session,
    error: str | None = None,
    status_code: int = 200,
):
    csrf_token = rotate_csrf(login_session)
    session.commit()
    rows = list_rows(session, owner_user_id=user.id)
    return render_template(
        request,
        "weight.html",
        status_code=status_code,
        title="Waga",
        user=user,
        csrf_token=csrf_token,
        error=error,
        today=today_utc().isoformat(),
        height_cm=get_height_cm(session, owner_user_id=user.id),
        rows=list(reversed(rows)),
        latest=rows[-1] if rows else None,
        chart=bmi_chart(rows),
        max_entries=MAX_ENTRIES,
    )


async def _csrf_checked_fields(
    request: Request, user: User, login_session: LoginSession
) -> dict[str, str]:
    fields = await read_form_fields(request, max_bytes=_MAX_FORM_BYTES, max_fields=_MAX_FORM_FIELDS)
    if login_session.user_id != user.id or not verify_csrf(
        login_session, fields.get("csrf_token") or request.headers.get("x-csrf-token")
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    return fields


@router.get("/ui", include_in_schema=False)
def weight_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    return _render_page(request, user=user, login_session=login_session, session=session)


@router.post("/ui", include_in_schema=False)
async def add_weight(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _csrf_checked_fields(request, user, login_session)
    try:
        weight_kg = parse_weight_kg(fields.get("weight_kg", ""))
        measured_on: date = parse_measured_on(fields.get("measured_on", ""), today=today_utc())
    except WeightError as error:
        return _render_page(
            request,
            user=user,
            login_session=login_session,
            session=session,
            error=str(error),
            status_code=422,
        )
    add_entry(session, owner_user_id=user.id, weight_kg=weight_kg, measured_on=measured_on)
    return RedirectResponse("/weight/ui", status_code=303)


@router.post("/height", include_in_schema=False)
async def save_height(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    fields = await _csrf_checked_fields(request, user, login_session)
    try:
        height_cm = parse_height_cm(fields.get("height_cm", ""))
    except WeightError as error:
        return _render_page(
            request,
            user=user,
            login_session=login_session,
            session=session,
            error=str(error),
            status_code=422,
        )
    set_height_cm(session, owner_user_id=user.id, height_cm=height_cm)
    return RedirectResponse("/weight/ui", status_code=303)


@router.post("/{resource_id}/delete", include_in_schema=False)
async def delete_weight(
    resource_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    await _csrf_checked_fields(request, user, login_session)
    if not delete_entry(session, owner_user_id=user.id, entry_id=resource_id):
        raise HTTPException(status_code=404, detail="Resource not found.")
    return RedirectResponse("/weight/ui", status_code=303)
