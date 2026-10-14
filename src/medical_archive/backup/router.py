import hmac
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from medical_archive.auth.dependencies import get_authenticated_session, require_admin
from medical_archive.auth.passwords import verify_password
from medical_archive.auth.service import rotate_csrf, validate_password, verify_csrf
from medical_archive.backup.service import (
    BackupBusyError,
    BackupService,
    BackupSourceError,
    BackupTooLargeError,
    backup_operation_lock,
)
from medical_archive.db import get_session
from medical_archive.models import LoginSession, User
from medical_archive.web.forms import read_form_fields
from medical_archive.web.templates import render_template

router = APIRouter(prefix="/settings/backup", tags=["backup"], include_in_schema=False)

PAGE_TITLE = "Kopia zapasowa"
EXPORT_SUMMARY = "Nie utworzono kopii. Popraw zaznaczone pola."
RESTORE_SUMMARY = "Nie odtworzono kopii. Obecne dane nie zostały zmienione."
WRONG_ADMIN_PASSWORD = "Hasło administratora jest nieprawidłowe."
BACKUP_PASSWORD_LENGTH = "Hasło kopii musi mieć od 12 do 1024 znaków."
BACKUP_PASSWORD_MISMATCH = "Hasła kopii nie są takie same."
EXPORT_CONFIRMATION_MISSING = "Zaznacz potwierdzenie, aby utworzyć kopię."
RESTORE_CONFIRMATION_MISSING = "Zaznacz potwierdzenie, aby odtworzyć kopię."
RESTORE_FILE_MISSING = "Wybierz plik kopii (.marchive)."
RESTORE_INVALID_BACKUP = (
    "Nie można odtworzyć kopii: plik jest uszkodzony albo hasło jest nieprawidłowe. "
    "Obecne dane nie zostały zmienione."
)
RESTORE_TOO_LARGE = (
    "Plik kopii jest za duży (maksymalnie {label}). Obecne dane nie zostały zmienione."
)
EXPORT_TOO_LARGE = "Nie można utworzyć kopii: dane przekraczają limit {label}."
RESTORE_MISSING_ADMINISTRATOR = (
    "Nie można odtworzyć kopii: nie zawiera aktywnego konta administratora. "
    "Obecne dane nie zostały zmienione."
)
RESTORE_UNSUPPORTED_REVISION = (
    "Nie można odtworzyć kopii: została utworzona w nowszej lub nieznanej wersji aplikacji. "
    "Zaktualizuj aplikację i spróbuj ponownie. Obecne dane nie zostały zmienione."
)
OPERATION_BUSY = "Operacja kopii zapasowej już trwa."
RESTORE_FAILED = (
    "Odtwarzanie nie powiodło się i zostało wycofane. Obecne dane nie zostały zmienione."
)
EXPORT_SOURCE_FAILED = "Nie można utworzyć kopii. Szczegóły zapisano w dzienniku aplikacji."

_BACKUP_TEMP_DIR = ".backup-tmp"
_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@router.get("", response_class=HTMLResponse)
def backup_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    return _render_backup_page(request, database, admin=admin, login_session=login_session)


@router.post("/export")
async def export_backup(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    service = _backup_service(request)
    form = await read_form_fields(request)
    if not verify_csrf(login_session, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")

    def render_export_error(
        status_code: int, alert: str, errors: dict[str, str] | None = None
    ) -> HTMLResponse:
        return _render_backup_page(
            request,
            database,
            admin=admin,
            login_session=login_session,
            status_code=status_code,
            export_alert=alert,
            export_errors=errors,
        )

    if not verify_password(admin.password_hash, form.get("admin_password", "")):
        return render_export_error(
            403, EXPORT_SUMMARY, {"export-admin-password": WRONG_ADMIN_PASSWORD}
        )
    backup_password = form.get("backup_password", "")
    errors = _export_field_errors(
        backup_password=backup_password,
        backup_password_repeat=form.get("backup_password_repeat", ""),
        confirmed=form.get("confirm") == "yes",
    )
    if errors:
        return render_export_error(400, EXPORT_SUMMARY, errors)

    destination = request.app.state.data_dir / _BACKUP_TEMP_DIR / f"{uuid.uuid4()}.marchive"
    try:
        with backup_operation_lock():
            destination.parent.mkdir(parents=True, exist_ok=True)
            await run_in_threadpool(
                service.create_backup,
                destination,
                backup_password,
                confirmed_all_profiles=True,
            )
    except BackupBusyError:
        return render_export_error(409, OPERATION_BUSY)
    except BackupTooLargeError:
        destination.unlink(missing_ok=True)
        return render_export_error(
            413, EXPORT_TOO_LARGE.format(label=_size_label(service.max_bytes))
        )
    except (BackupSourceError, OSError):
        destination.unlink(missing_ok=True)
        return render_export_error(500, EXPORT_SOURCE_FAILED)
    filename = f"medyczne-archiwum-{datetime.now(UTC):%Y%m%d-%H%M%S}.marchive"
    return FileResponse(
        destination,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(_remove_file, destination),
    )


def _export_field_errors(
    *, backup_password: str, backup_password_repeat: str, confirmed: bool
) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        validate_password(backup_password)
    except ValueError:
        errors["export-backup-password"] = BACKUP_PASSWORD_LENGTH
    if not hmac.compare_digest(
        backup_password.encode("utf-8"), backup_password_repeat.encode("utf-8")
    ):
        errors["export-backup-password-repeat"] = BACKUP_PASSWORD_MISMATCH
    if not confirmed:
        errors["export-confirm"] = EXPORT_CONFIRMATION_MISSING
    return errors


def _render_backup_page(
    request: Request,
    database: Session,
    *,
    admin: User,
    login_session: LoginSession,
    status_code: int = 200,
    export_alert: str | None = None,
    export_errors: dict[str, str] | None = None,
    restore_alert: str | None = None,
    restore_errors: dict[str, str] | None = None,
) -> HTMLResponse:
    service = _backup_service(request)
    csrf_token = rotate_csrf(login_session)
    database.commit()
    return render_template(
        request,
        "backup.html",
        status_code=status_code,
        title=PAGE_TITLE,
        user=admin,
        csrf_token=csrf_token,
        export_alert=export_alert,
        export_errors=export_errors or {},
        restore_alert=restore_alert,
        restore_errors=restore_errors or {},
        max_backup_size_label=_size_label(service.max_bytes),
    )


def _backup_service(request: Request) -> BackupService:
    service = getattr(request.app.state, "backup_service", None)
    if service is None:
        raise RuntimeError("The backup service is not configured for this application.")
    return service


def _size_label(max_bytes: int) -> str:
    if max_bytes % _GIB == 0:
        return f"{max_bytes // _GIB} GiB"
    return f"{max_bytes // _MIB} MiB"


def _remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


__all__ = ["router"]
