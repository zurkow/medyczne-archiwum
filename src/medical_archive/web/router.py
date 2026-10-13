from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import get_authenticated_session, get_current_user
from medical_archive.auth.service import rotate_csrf
from medical_archive.db import get_session
from medical_archive.models import LoginSession, User
from medical_archive.web.templates import render_template

router = APIRouter()


@router.get("/", include_in_schema=False)
def home(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    csrf_token = rotate_csrf(login_session)
    database.commit()
    return render_template(
        request,
        "base.html",
        title="Medyczne Archiwum",
        user=user,
        csrf_token=csrf_token,
    )


__all__ = ["router"]
