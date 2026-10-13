from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from medical_archive.auth.service import token_hash
from medical_archive.db import get_session
from medical_archive.models import LoginSession, User


def get_authenticated_session(
    request: Request,
    database: Annotated[Session, Depends(get_session)],
) -> LoginSession:
    raw_token = request.cookies.get("session")
    if not raw_token:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    login_session = database.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == token_hash(raw_token),
            LoginSession.user_id.is_not(None),
            LoginSession.purpose == "authenticated",
            LoginSession.expires_at > datetime.now(UTC),
        )
    )
    if login_session is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return login_session


def get_current_user(
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
) -> User:
    user = database.get(User, login_session.user_id)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user
