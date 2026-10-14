from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from medical_archive.auth.dependencies import get_authenticated_session, get_current_user
from medical_archive.auth.service import rotate_csrf, verify_csrf
from medical_archive.db import get_session
from medical_archive.documents.service import create_answer_service
from medical_archive.models import LoginSession, User
from medical_archive.rag.retrieval import RetrievalError
from medical_archive.rag.service import (
    AnswerMeasurementSource,
    AnswerResult,
    AnswerServiceError,
)
from medical_archive.web.templates import render_template

router = APIRouter(prefix="/assistant", tags=["assistant"])
_MAX_FORM_BYTES = 8192


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a question.")
        return value.strip()


def _service_for_request(request: Request, session: Session):
    configured = getattr(request.app.state, "answer_service", None)
    return configured if configured is not None else create_answer_service(session)


def _verify_request_csrf(
    request: Request,
    user: User,
    login_session: LoginSession,
    token: str | None = None,
) -> None:
    supplied_token = request.headers.get("x-csrf-token") or token
    if (
        login_session.user_id != user.id
        or not isinstance(supplied_token, str)
        or not verify_csrf(login_session, supplied_token)
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


def _answer_payload(result: AnswerResult) -> dict[str, object]:
    sources = []
    for source in result.sources:
        if isinstance(source, AnswerMeasurementSource):
            sources.append(
                {
                    "kind": "measurement",
                    "citation_id": source.citation_id,
                    "measurement_id": source.measurement_id,
                    "name": source.name,
                    "value": source.value,
                    "unit": source.unit,
                    "measured_at": source.measured_at.isoformat().replace("+00:00", "Z"),
                    "url": source.url,
                }
            )
        else:
            sources.append(
                {
                    "kind": "document",
                    "citation_id": source.citation_id,
                    "document_id": source.document_id,
                    "filename": source.filename,
                    "page_number": source.page_number,
                    "url": source.url,
                }
            )
    return {
        "answer": result.answer,
        "sources": sources,
    }


def _run_answer(service, *, user_id: int, question: str) -> AnswerResult:
    try:
        return service.ask(user_id=user_id, question=question)
    except RetrievalError as error:
        raise HTTPException(
            status_code=503,
            detail="Lokalne wyszukiwanie dokumentów jest niedostępne.",
        ) from error
    except AnswerServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.public_message) from error


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def assistant_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    csrf_token = rotate_csrf(login_session)
    session.commit()
    return render_template(
        request,
        "chat.html",
        title="Asystent",
        user=user,
        csrf_token=csrf_token,
        question="",
        answer=None,
        sources=[],
        error=None,
    )


@router.post("", response_class=JSONResponse)
def ask_json(
    payload: AskRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    _verify_request_csrf(request, user, login_session)
    result = _run_answer(
        _service_for_request(request, session),
        user_id=user.id,
        question=payload.question,
    )
    return _answer_payload(result)


@router.post("/ask", response_class=HTMLResponse, include_in_schema=False)
async def ask_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    session: Annotated[Session, Depends(get_session)],
):
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0]
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Unsupported form content type.")
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Form data is too large.")
    try:
        fields = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=4,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid form data.") from error
    if any(len(values) != 1 for values in fields.values()):
        raise HTTPException(status_code=400, detail="Duplicate form fields are not allowed.")
    question = fields.get("question", [""])[0].strip()
    csrf_token = fields.get("csrf_token", [""])[0]
    _verify_request_csrf(request, user, login_session, csrf_token)
    if not question or len(question) > 2000:
        return render_template(
            request,
            "chat.html",
            status_code=422,
            title="Asystent",
            user=user,
            csrf_token=csrf_token,
            question=question[:2000],
            answer=None,
            sources=[],
            error="Wpisz pytanie o długości do 2000 znaków.",
        )
    try:
        result = _run_answer(
            _service_for_request(request, session),
            user_id=user.id,
            question=question,
        )
    except HTTPException as error:
        return render_template(
            request,
            "chat.html",
            status_code=error.status_code,
            title="Asystent",
            user=user,
            csrf_token=csrf_token,
            question=question,
            answer=None,
            sources=[],
            error=error.detail,
        )
    return render_template(
        request,
        "chat.html",
        title="Asystent",
        user=user,
        csrf_token=csrf_token,
        question=question,
        answer=result.answer,
        sources=result.sources,
        error=None,
    )
