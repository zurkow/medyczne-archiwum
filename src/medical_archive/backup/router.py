import hmac
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from medical_archive.auth.dependencies import get_authenticated_session, require_admin
from medical_archive.auth.passwords import verify_password
from medical_archive.auth.service import rotate_csrf, validate_password, verify_csrf
from medical_archive.backup.crypto import InvalidBackup
from medical_archive.backup.maintenance import (
    DRAIN_TIMEOUT_SECONDS,
    MaintenanceState,
    maintenance_page,
)
from medical_archive.backup.service import (
    BackupBusyError,
    BackupService,
    BackupSourceError,
    BackupTooLargeError,
    MissingAdministratorError,
    RestoreFailedError,
    RestoreRollbackFailedError,
    UnsupportedBackupRevisionError,
    backup_operation_lock,
)
from medical_archive.db import get_session
from medical_archive.documents.storage import UploadError, read_multipart_form
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
_UPLOAD_NAME = "upload.marchive"
_RESTORE_TEXT_FIELDS = frozenset({"csrf_token", "backup_password", "admin_password", "confirm"})
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


@router.post("/restore")
async def restore_backup(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    login_session: Annotated[LoginSession, Depends(get_authenticated_session)],
    database: Annotated[Session, Depends(get_session)],
):
    service = _backup_service(request)
    try:
        with backup_operation_lock():
            operation_dir = service.new_operation_dir()
            try:
                return await _restore_from_upload(
                    request,
                    database,
                    service=service,
                    admin=admin,
                    login_session=login_session,
                    upload_path=operation_dir / _UPLOAD_NAME,
                )
            finally:
                service.discard(operation_dir)
    except BackupBusyError:
        return _render_backup_page(
            request,
            database,
            admin=admin,
            login_session=login_session,
            status_code=409,
            restore_alert=OPERATION_BUSY,
        )


async def _restore_from_upload(
    request: Request,
    database: Session,
    *,
    service: BackupService,
    admin: User,
    login_session: LoginSession,
    upload_path: Path,
):
    maintenance: MaintenanceState = request.app.state.maintenance

    def render_restore_error(
        status_code: int, alert: str, errors: dict[str, str] | None = None
    ) -> HTMLResponse:
        return _render_backup_page(
            request,
            database,
            admin=admin,
            login_session=login_session,
            status_code=status_code,
            restore_alert=alert,
            restore_errors=errors,
        )

    def require_csrf(fields: Mapping[str, str]) -> None:
        if not verify_csrf(login_session, fields.get("csrf_token")):
            raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")

    try:
        form = await read_multipart_form(
            request,
            text_fields=_RESTORE_TEXT_FIELDS,
            file_field="backup_file",
            destination=upload_path,
            max_file_bytes=service.max_bytes,
            before_file=require_csrf,
        )
    except UploadError as error:
        if error.status_code == 413:
            message = RESTORE_TOO_LARGE.format(label=_size_label(service.max_bytes))
            return render_restore_error(413, message, {"restore-file": message})
        return render_restore_error(415 if error.status_code == 415 else 400, RESTORE_SUMMARY)

    errors: dict[str, str] = {}
    if form.file_size == 0:
        errors["restore-file"] = RESTORE_FILE_MISSING
    if form.fields.get("confirm") != "yes":
        errors["restore-confirm"] = RESTORE_CONFIRMATION_MISSING
    if errors:
        return render_restore_error(400, RESTORE_SUMMARY, errors)
    if not verify_password(admin.password_hash, form.fields.get("admin_password", "")):
        return render_restore_error(
            403, RESTORE_SUMMARY, {"restore-admin-password": WRONG_ADMIN_PASSWORD}
        )

    try:
        staged = await run_in_threadpool(
            service.stage_restore, upload_path, form.fields.get("backup_password", "")
        )
    except UnsupportedBackupRevisionError:
        return render_restore_error(422, RESTORE_UNSUPPORTED_REVISION)
    except MissingAdministratorError:
        return render_restore_error(422, RESTORE_MISSING_ADMINISTRATOR)
    except InvalidBackup:
        return render_restore_error(422, RESTORE_INVALID_BACKUP)
    except RestoreFailedError:
        return render_restore_error(500, RESTORE_FAILED)

    database.commit()
    maintenance.enter()
    if not await maintenance.wait_until_drained(timeout_seconds=DRAIN_TIMEOUT_SECONDS):
        maintenance.exit()
        return render_restore_error(500, RESTORE_FAILED)
    try:
        await run_in_threadpool(service.apply_restore, staged)
    except RestoreRollbackFailedError:
        return maintenance_page(status_code=500, rollback_failed=True)
    except RestoreFailedError:
        maintenance.exit()
        return render_restore_error(500, RESTORE_FAILED)
    except Exception:
        # Only a failed rollback may keep the app locked; anything else must not strand it.
        maintenance.exit()
        raise
    maintenance.exit()
    response = RedirectResponse("/auth/login?restored=1", status_code=303)
    response.delete_cookie(
        "session",
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


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
