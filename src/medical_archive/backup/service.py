import logging
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from medical_archive.auth.service import validate_password
from medical_archive.backup import crypto
from medical_archive.backup import format as archive_format

logger = logging.getLogger(__name__)

_BACKUP_TEMP_DIR = ".backup-tmp"
_ENTRY_OVERHEAD_BYTES = 4 * 1024
_ARCHIVE_OVERHEAD_BYTES = 64 * 1024


class BackupConfirmationRequired(Exception):
    """Raised when a full backup is requested without confirming it covers all profiles."""


class BackupTooLargeError(Exception):
    """Raised when a backup would exceed the configured size limit."""


class BackupSourceError(Exception):
    """Raised when the live database or an original file cannot be read for a backup."""


def create_backup(
    destination: Path,
    password: str,
    *,
    data_dir: Path,
    database_url: str,
    confirmed_all_profiles: bool = False,
    max_bytes: int | None = None,
) -> str:
    if not confirmed_all_profiles:
        raise BackupConfirmationRequired("A full backup must be confirmed for all profiles.")
    validate_password(password)
    limit = crypto.MAX_ENVELOPE_BYTES if max_bytes is None else max_bytes
    started = time.monotonic()
    work_dir = data_dir / _BACKUP_TEMP_DIR / str(uuid.uuid4())
    try:
        live_path = _sqlite_path(database_url)
        work_dir.mkdir(parents=True)
        snapshot_path = work_dir / "snapshot.sqlite3"
        _snapshot_database(live_path, snapshot_path)
        schema_revision, storage_keys = _read_backup_source(snapshot_path)
        documents_dir = data_dir / "documents"
        files_size = sum(_original_file_size(documents_dir / key) for key in storage_keys)
        estimate = (
            snapshot_path.stat().st_size
            + files_size
            + _ENTRY_OVERHEAD_BYTES * (len(storage_keys) + 2)
            + _ARCHIVE_OVERHEAD_BYTES
        )
        if estimate > limit:
            raise BackupTooLargeError("The backup would exceed the size limit.")
        archive_path = work_dir / "archive.zip"
        manifest = archive_format.write_archive(
            archive_path,
            database_path=snapshot_path,
            documents_dir=documents_dir,
            storage_keys=storage_keys,
            schema_revision=schema_revision,
        )
        envelope = crypto.encrypt(archive_path.read_bytes(), password)
        if len(envelope) > limit:
            raise BackupTooLargeError("The backup would exceed the size limit.")
        _write_atomic(destination, envelope)
    except Exception as error:
        logger.warning("backup_export_failed code=%s", type(error).__name__)
        raise
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir)
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "backup_export_succeeded archive_id=%s duration_ms=%d",
        manifest["archive_id"],
        duration_ms,
    )
    return manifest["archive_id"]


def _sqlite_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
    except ArgumentError as error:
        raise BackupSourceError("The database URL is invalid.") from error
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise BackupSourceError("Backups support only a file-based SQLite database.")
    return Path(url.database)


def _snapshot_database(live_path: Path, snapshot_path: Path) -> None:
    if not live_path.is_file():
        raise BackupSourceError("The live database file does not exist.")
    try:
        source = sqlite3.connect(live_path)
        try:
            target = sqlite3.connect(snapshot_path)
            try:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                target.close()
        finally:
            source.close()
    except sqlite3.Error as error:
        raise BackupSourceError("The live database could not be copied.") from error


def _read_backup_source(snapshot_path: Path) -> tuple[str, list[str]]:
    connection = sqlite3.connect(snapshot_path)
    try:
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        storage_keys = [
            row[0]
            for row in connection.execute("SELECT storage_key FROM documents ORDER BY storage_key")
        ]
    except sqlite3.Error as error:
        raise BackupSourceError("The database snapshot could not be read.") from error
    finally:
        connection.close()
    if len(revisions) != 1 or not isinstance(revisions[0][0], str):
        raise BackupSourceError("The database must record exactly one schema revision.")
    if not all(archive_format.is_storage_key(key) for key in storage_keys):
        raise BackupSourceError("The database contains an invalid storage key.")
    return revisions[0][0], storage_keys


def _original_file_size(path: Path) -> int:
    try:
        status = path.lstat()
    except OSError as error:
        raise BackupSourceError("A referenced original file is missing.") from error
    if not stat.S_ISREG(status.st_mode):
        raise BackupSourceError("A referenced original is not a regular file.")
    return status.st_size


def _write_atomic(path: Path, data: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".backup-",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
