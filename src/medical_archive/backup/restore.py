import argparse
import getpass
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from medical_archive.backup.crypto import InvalidBackup
from medical_archive.backup.service import (
    BackupService,
    MissingAdministratorError,
    RestoreFailedError,
    RestoreRollbackFailedError,
    UnsupportedBackupRevisionError,
    backup_operation_lock,
    recover_backup_state,
)
from medical_archive.config import Settings

CONFIRMATION_WORD = "RESTORE"
WARNING = (
    "WARNING: stop the Medyczne Archiwum server before restoring. "
    "All current data, original files and sessions will be replaced by the backup."
)
UNCHANGED = "The current data was not changed."
CANCELLED = f"Restore cancelled. {UNCHANGED}"
BACKUP_FILE_MISSING = f"The backup file does not exist. {UNCHANGED}"
BACKUP_FILE_UNREADABLE = f"The backup file could not be read. {UNCHANGED}"
BACKUP_TOO_LARGE = f"The backup file is too large (maximum {{limit}} bytes). {UNCHANGED}"
INVALID_BACKUP = (
    f"The backup cannot be restored: the file is damaged or the password is wrong. {UNCHANGED}"
)
MISSING_ADMINISTRATOR = (
    "The backup cannot be restored: it does not contain an active administrator account. "
    f"{UNCHANGED}"
)
UNSUPPORTED_REVISION = (
    "The backup cannot be restored: it was created by a newer or unknown version of the "
    f"application. Update the application and try again. {UNCHANGED}"
)
RESTORE_FAILED = f"The restore failed and was rolled back. {UNCHANGED}"
ROLLBACK_FAILED = (
    "The restore failed and the rollback did not finish. Keep the data directory unchanged; "
    "the rollback is retried when the application or this command starts next."
)
RECOVERY_FAILED = (
    "An interrupted restore could not be recovered. Keep the data directory unchanged and "
    "follow the recovery steps in the README."
)
RESTORE_COMPLETED = "Restore completed. All sessions were signed out."
_UPLOAD_NAME = "upload.marchive"


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Restore an encrypted Medyczne Archiwum backup while the server is stopped."
    )
    parser.add_argument(
        "--backup-file", required=True, type=Path, help="Path to the .marchive backup file."
    )
    options = parser.parse_args(arguments)

    settings = Settings()
    try:
        recover_backup_state(data_dir=settings.data_dir, database_url=settings.database_url)
    except RestoreFailedError:
        _fail(RECOVERY_FAILED)

    print(WARNING)
    if input(f"Type {CONFIRMATION_WORD} to continue: ") != CONFIRMATION_WORD:
        _fail(CANCELLED)
    backup_file: Path = options.backup_file
    if not backup_file.is_file():
        _fail(BACKUP_FILE_MISSING)
    if backup_file.stat().st_size > settings.backup_max_bytes:
        _fail(BACKUP_TOO_LARGE.format(limit=settings.backup_max_bytes))
    password = getpass.getpass("Backup password: ")

    service = BackupService(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        max_bytes=settings.backup_max_bytes,
        dispose_engine=lambda: None,
    )
    try:
        with backup_operation_lock():
            _restore(service, backup_file, password)
    except UnsupportedBackupRevisionError:
        _fail(UNSUPPORTED_REVISION)
    except MissingAdministratorError:
        _fail(MISSING_ADMINISTRATOR)
    except InvalidBackup:
        _fail(INVALID_BACKUP)
    except RestoreRollbackFailedError:
        _fail(ROLLBACK_FAILED)
    except RestoreFailedError:
        _fail(RESTORE_FAILED)
    print(RESTORE_COMPLETED)


def _restore(service: BackupService, backup_file: Path, password: str) -> None:
    operation_dir = service.new_operation_dir()
    try:
        upload_path = operation_dir / _UPLOAD_NAME
        try:
            shutil.copyfile(backup_file, upload_path)
        except OSError:
            _fail(BACKUP_FILE_UNREADABLE)
        staged = service.stage_restore(upload_path, password)
        service.apply_restore(staged)
    finally:
        service.discard(operation_dir)


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
