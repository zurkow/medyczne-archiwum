from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import (
    get_authenticated_session,
    require_admin,
)
from medical_archive.auth.service import (
    PREAUTH_LIFETIME,
    SESSION_LIFETIME,
    AuthorizationError,
    DuplicateEmailError,
    InvalidInvitationError,
    authenticate_user,
    create_invitation,
    create_preauth_session,
    find_valid_invitation,
    find_valid_session,
    register_with_invitation,
    revoke_session,
    rotate_csrf,
    start_authenticated_session,
    verify_csrf,
)
from medical_archive.db import get_session
from medical_archive.models import LoginSession, User
from medical_archive.web.forms import read_form_fields
from medical_archive.web.templates import render_template

router = APIRouter(prefix="/auth", tags=["auth"])
_PREAUTH_MAX_AGE = int(PREAUTH_LIFETIME.total_seconds())
_SESSION_MAX_AGE = int(SESSION_LIFETIME.total_seconds())


def _set_session_cookie(response, token: str, *, max_age: int) -> None:
    response.set_cookie(
        "session",
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def _csrf_or_forbidden(record: LoginSession | None, submitted_token: str | None) -> None:
    if record is None or not verify_csrf(record, submitted_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


def _issue_preauth_form(
    request: Request,
    database: Session,
    *,
    purpose: str,
) -> tuple[str, str, bool]:
    session_token = request.cookies.get("session")
    record = find_valid_session(
        database,
        session_token,
        allow_preauth=True,
        purpose=purpose,
    )
    if record is not None and record.user_id is None:
        csrf_token = rotate_csrf(record)
        record.expires_at = datetime.now(UTC) + PREAUTH_LIFETIME
        database.commit()
        return session_token, csrf_token, False
    session_token, csrf_token, _record = create_preauth_session(
        database,
        purpose=purpose,
    )
    return session_token, csrf_token, True


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(
    request: Request,
    database: Annotated[Session, Depends(get_session)],
    restored: str | None = None,
):
    existing = find_valid_session(
        database,
        request.cookies.get("session"),
        purpose="authenticated",
    )
    if existing is not None:
        return RedirectResponse("/", status_code=303)
    session_token, csrf_token, new_session = _issue_preauth_form(
        request,
        database,
        purpose="login",
    )
    response = render_template(
        request,
        "login.html",
        title="Zaloguj się",
        csrf_token=csrf_token,
        error=None,
        notice="restored" if restored == "1" else None,
    )
    if new_session:
        _set_session_cookie(response, session_token, max_age=_PREAUTH_MAX_AGE)
    return response


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(
    request: Request,
    database: Annotated[Session, Depends(get_session)],
):
    form = await read_form_fields(request)
    email = form.get("email", "")
    password = form.get("password", "")
    csrf_token = form.get("csrf_token")
    preauth_session = find_valid_session(
        database,
        request.cookies.get("session"),
        allow_preauth=True,
        purpose="login",
    )
    if preauth_session is None or preauth_session.user_id is not None:
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    _csrf_or_forbidden(preauth_session, csrf_token)
    user = authenticate_user(database, email, password)
    if user is None:
        return render_template(
            request,
            "login.html",
            title="Zaloguj się",
            csrf_token=csrf_token,
            error="Nieprawidłowy adres e-mail lub hasło.",
            status_code=401,
        )
    session_token, _csrf_token = start_authenticated_session(
        database,
        preauth_session=preauth_session,
        user=user,
    )
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, session_token, max_age=_SESSION_MAX_AGE)
    return response


@router.post("/logout", include_in_schema=False)
async def logout(
    request: Request,
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    _csrf_or_forbidden(login_session, (await read_form_fields(request)).get("csrf_token"))
    revoke_session(database, login_session)
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(
        "session",
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/invite", response_class=HTMLResponse, include_in_schema=False)
def invitation_page(
    request: Request,
    _admin: Annotated[User, Depends(require_admin)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    csrf_token = rotate_csrf(login_session)
    database.commit()
    return render_template(
        request,
        "invite.html",
        title="Zaproś użytkownika",
        csrf_token=csrf_token,
        invitation_url=None,
        error=None,
    )


@router.post("/invite", response_class=HTMLResponse, include_in_schema=False)
async def create_invite(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    form = await read_form_fields(request)
    _csrf_or_forbidden(login_session, form.get("csrf_token"))
    try:
        invitation_token, _invitation = create_invitation(database, creator=admin)
    except AuthorizationError as error:
        raise HTTPException(status_code=403, detail="Administrator access is required.") from error
    invitation_url = str(
        request.url_for("register_page").include_query_params(token=invitation_token)
    )
    return render_template(
        request,
        "invite.html",
        title="Zaproszenie utworzone",
        csrf_token=None,
        invitation_url=invitation_url,
        error=None,
    )


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
def register_page(
    request: Request,
    database: Annotated[Session, Depends(get_session)],
    token: str = "",
):
    if find_valid_invitation(database, token) is None:
        raise HTTPException(status_code=400, detail="This invitation is invalid or expired.")
    session_token = request.cookies.get("session")
    record = find_valid_session(
        database,
        session_token,
        purpose="authenticated",
    )
    if record is None:
        record = find_valid_session(
            database,
            session_token,
            allow_preauth=True,
            purpose="registration",
        )
    if record is None:
        session_token, csrf_token, new_session = _issue_preauth_form(
            request,
            database,
            purpose="registration",
        )
    else:
        csrf_token = rotate_csrf(record)
        database.commit()
        new_session = False
    response = render_template(
        request,
        "register.html",
        title="Utwórz konto",
        invitation_token=token,
        csrf_token=csrf_token,
        error=None,
    )
    if new_session:
        _set_session_cookie(response, session_token, max_age=_PREAUTH_MAX_AGE)
    return response


@router.post("/register", response_class=HTMLResponse, include_in_schema=False)
async def register(
    request: Request,
    database: Annotated[Session, Depends(get_session)],
):
    form = await read_form_fields(request)
    token = form.get("token", "")
    email = form.get("email", "")
    password = form.get("password", "")
    csrf_token = form.get("csrf_token")
    record = find_valid_session(
        database,
        request.cookies.get("session"),
        allow_preauth=True,
    )
    _csrf_or_forbidden(record, csrf_token)
    if record.user_id is None and record.purpose != "registration":
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    try:
        register_with_invitation(
            database,
            raw_token=token,
            email=email,
            password=password,
        )
    except InvalidInvitationError as error:
        raise HTTPException(
            status_code=400, detail="This invitation is invalid or expired."
        ) from error
    except DuplicateEmailError as error:
        raise HTTPException(
            status_code=400, detail="An account already exists for this email."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response = RedirectResponse("/auth/login", status_code=303)
    if record.user_id is None:
        revoke_session(database, record)
        response.delete_cookie(
            "session",
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
    return response


__all__ = ["router"]
