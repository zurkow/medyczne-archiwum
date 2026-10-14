import json
import logging
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from medical_archive.auth.service import validate_password
from medical_archive.backup import crypto
from medical_archive.backup import format as archive_format
from medical_archive.backup.crypto import InvalidBackup
from medical_archive.rag.index import VectorExtensionUnavailableError, load_sqlite_vec

logger = logging.getLogger(__name__)

_BACKUP_TEMP_DIR = ".backup-tmp"
_RESTORE_STAGING_DIR = ".restore-staging"
_JOURNAL_NAME = ".restore-journal.json"
_JOURNAL_VERSION = 1
_JOURNAL_KEYS = frozenset({"journal_version", "operation_id", "state", "live_documents_existed"})
_UNRESOLVED_STATES = frozenset({"prepared", "database_replaced", "documents_replaced"})
_COMMITTED_STATE = "committed"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OPERATION_LOCK = threading.Lock()
_ENTRY_OVERHEAD_BYTES = 4 * 1024
_ARCHIVE_OVERHEAD_BYTES = 64 * 1024


class BackupConfirmationRequired(Exception):
    """Raised when a full backup is requested without confirming it covers all profiles."""


class BackupTooLargeError(Exception):
    """Raised when a backup would exceed the configured size limit."""


class BackupSourceError(Exception):
    """Raised when the live database or an original file cannot be read for a backup."""


class BackupBusyError(Exception):
    """Raised when another backup or restore operation is already running."""


class UnsupportedBackupRevisionError(InvalidBackup):
    """Raised when a backup was created by a newer or unknown application schema."""


class MissingAdministratorError(InvalidBackup):
    """Raised when a backup does not contain an administrator account."""


class RestoreFailedError(Exception):
    """Raised when a restore failed and the previous data was kept or rolled back."""


class RestoreRollbackFailedError(RestoreFailedError):
    """Raised when rolling back a failed restore failed and must finish at startup."""


@dataclass(frozen=True)
class StagedRestore:
    operation_dir: Path
    database_path: Path
    documents_dir: Path
    archive_id: str
    schema_revision: str


class BackupService:
    def __init__(
        self,
        *,
        data_dir: Path,
        database_url: str,
        max_bytes: int,
        dispose_engine: Callable[[], None],
    ) -> None:
        self._data_dir = data_dir
        self._database_url = database_url
        self._max_bytes = max_bytes
        self._dispose_engine = dispose_engine

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def create_backup(
        self, destination: Path, password: str, *, confirmed_all_profiles: bool
    ) -> str:
        return create_backup(
            destination,
            password,
            data_dir=self._data_dir,
            database_url=self._database_url,
            confirmed_all_profiles=confirmed_all_profiles,
            max_bytes=self._max_bytes,
        )

    def new_operation_dir(self) -> Path:
        staging_root = self._data_dir / _RESTORE_STAGING_DIR
        staging_root.mkdir(parents=True, exist_ok=True)
        operation_dir = staging_root / str(uuid.uuid4())
        operation_dir.mkdir()
        return operation_dir

    def stage_restore(self, envelope_path: Path, password: str) -> StagedRestore:
        return stage_restore(
            envelope_path,
            password,
            data_dir=self._data_dir,
            max_bytes=self._max_bytes,
        )

    def apply_restore(self, staged: StagedRestore) -> None:
        apply_restore(
            staged,
            data_dir=self._data_dir,
            database_url=self._database_url,
            dispose_engine=self._dispose_engine,
        )

    def discard(self, operation_dir: Path) -> None:
        operation_id = _operation_id(self._data_dir, operation_dir)
        journal = _read_journal(self._data_dir)
        if journal is not None and journal["operation_id"] == operation_id:
            return
        if operation_dir.exists():
            shutil.rmtree(operation_dir)


@contextmanager
def backup_operation_lock() -> Iterator[None]:
    if not _OPERATION_LOCK.acquire(blocking=False):
        raise BackupBusyError("Another backup operation is already running.")
    try:
        yield
    finally:
        _OPERATION_LOCK.release()


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


def stage_restore(
    envelope_path: Path,
    password: str,
    *,
    data_dir: Path,
    max_bytes: int,
) -> StagedRestore:
    operation_dir = envelope_path.parent
    _operation_id(data_dir, operation_dir)
    try:
        return _stage(envelope_path, password, operation_dir=operation_dir, max_bytes=max_bytes)
    except Exception as error:
        logger.warning("backup_restore_rejected code=%s", type(error).__name__)
        if isinstance(error, OSError):
            raise RestoreFailedError("The backup could not be staged.") from error
        raise


def apply_restore(
    staged: StagedRestore,
    *,
    data_dir: Path,
    database_url: str,
    dispose_engine: Callable[[], None],
) -> None:
    started = time.monotonic()
    operation_id = _operation_id(data_dir, staged.operation_dir)
    live_path = _live_database_path(database_url)
    documents_dir = data_dir / "documents"
    rollback_dir = staged.operation_dir / "rollback"
    dispose_engine()
    live_documents_existed = documents_dir.exists()

    def record(state: str) -> None:
        _write_journal(
            data_dir,
            operation_id=operation_id,
            state=state,
            live_documents_existed=live_documents_existed,
        )

    try:
        rollback_dir.mkdir()
        _snapshot_live_database(live_path, rollback_dir / "database.sqlite3")
        record("prepared")
    except (OSError, BackupSourceError) as error:
        logger.warning("backup_restore_failed code=%s", type(error).__name__)
        raise RestoreFailedError("The live data could not be prepared for a restore.") from error

    try:
        _copy_staged_database(staged.database_path, live_path)
        record("database_replaced")
        _swap_documents(documents_dir, rollback_dir / "documents", staged.documents_dir)
        record("documents_replaced")
        _wipe_sessions(live_path)
        _health_check(live_path, documents_dir)
        record(_COMMITTED_STATE)
    except Exception as error:
        _rollback(
            data_dir,
            live_path,
            operation_id=operation_id,
            live_documents_existed=live_documents_existed,
            code=type(error).__name__,
        )
        raise RestoreFailedError(
            "The restore failed and the previous data was restored."
        ) from error

    _finish_committed(data_dir, staged.operation_dir)
    logger.info(
        "backup_restore_applied archive_id=%s duration_ms=%d",
        staged.archive_id,
        int((time.monotonic() - started) * 1000),
    )


def recover_backup_state(*, data_dir: Path, database_url: str) -> None:
    journal = _read_journal(data_dir)
    if journal is not None:
        operation_dir = data_dir / _RESTORE_STAGING_DIR / journal["operation_id"]
        if journal["state"] == _COMMITTED_STATE:
            _finish_committed(data_dir, operation_dir)
        else:
            _rollback(
                data_dir,
                _live_database_path(database_url),
                operation_id=journal["operation_id"],
                live_documents_existed=journal["live_documents_existed"],
                code="interrupted",
            )
    unresolved = _read_journal(data_dir)
    for directory_name in (_BACKUP_TEMP_DIR, _RESTORE_STAGING_DIR):
        root = data_dir / directory_name
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if unresolved is not None and entry.name == unresolved["operation_id"]:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()


def _stage(
    envelope_path: Path,
    password: str,
    *,
    operation_dir: Path,
    max_bytes: int,
) -> StagedRestore:
    if envelope_path.stat().st_size > max_bytes:
        raise InvalidBackup("The backup file exceeds the size limit.")
    envelope = envelope_path.read_bytes()
    envelope_path.unlink()
    payload = crypto.decrypt(envelope, password)
    del envelope
    staged_dir = operation_dir / "staged"
    manifest = archive_format.extract_archive(payload, staged_dir)
    del payload
    database_path = staged_dir / archive_format.DATABASE_NAME
    _check_database_integrity(database_path)
    schema_revision = _bring_to_head(database_path)

    connection = sqlite3.connect(database_path)
    try:
        try:
            admin_count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1"
            ).fetchone()[0]
            storage_keys = [
                row[0] for row in connection.execute("SELECT storage_key FROM documents")
            ]
        except sqlite3.Error as error:
            raise InvalidBackup("The backup database could not be read.") from error
        if admin_count < 1:
            raise MissingAdministratorError("The backup has no administrator account.")
        try:
            load_sqlite_vec(connection)
            connection.execute("SELECT COUNT(*) FROM rag_chunk_vectors").fetchone()
        except VectorExtensionUnavailableError as error:
            raise RestoreFailedError("sqlite-vec is required to verify a backup.") from error
        except sqlite3.Error as error:
            raise InvalidBackup("The backup vector index could not be read.") from error
    finally:
        connection.close()
    if sorted(storage_keys) != manifest["storage_keys"]:
        raise InvalidBackup("The backup documents do not match the manifest.")
    return StagedRestore(
        operation_dir=operation_dir,
        database_path=database_path,
        documents_dir=staged_dir / "documents",
        archive_id=manifest["archive_id"],
        schema_revision=schema_revision,
    )


def _check_database_integrity(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_key_problems = connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error as error:
        raise InvalidBackup("The backup database is damaged.") from error
    finally:
        connection.close()
    if integrity != [("ok",)] or foreign_key_problems:
        raise InvalidBackup("The backup database is damaged.")


def _bring_to_head(database_path: Path) -> str:
    script = ScriptDirectory.from_config(_alembic_config())
    head = script.get_current_head()
    revision = _read_revision(database_path)
    if revision == head:
        return head
    known_revisions = {migration.revision for migration in script.walk_revisions()}
    if revision is None or revision not in known_revisions:
        raise UnsupportedBackupRevisionError("The backup schema revision is not supported.")
    try:
        command.upgrade(_alembic_config(database_path), "head")
    except (OSError, VectorExtensionUnavailableError) as error:
        raise RestoreFailedError("The backup schema could not be upgraded.") from error
    except Exception as error:
        raise InvalidBackup("The backup schema could not be upgraded.") from error
    _check_database_integrity(database_path)
    if _read_revision(database_path) != head:
        raise InvalidBackup("The backup schema could not be upgraded.")
    return head


def _read_revision(database_path: Path) -> str | None:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        return None
    return rows[0][0]


def _alembic_config(database_path: Path | None = None) -> Config:
    # No ini file: the logging setup in alembic.ini would disable the application's loggers.
    config = Config()
    config.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    if database_path is not None:
        url = URL.create("sqlite", database=database_path.as_posix()).render_as_string()
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _live_database_path(database_url: str) -> Path:
    try:
        return _sqlite_path(database_url)
    except BackupSourceError as error:
        raise RestoreFailedError("Restore supports only a file-based SQLite database.") from error


def _snapshot_live_database(live_path: Path, target: Path) -> None:
    partial = target.with_name(target.name + ".partial")
    _snapshot_database(live_path, partial)
    partial.replace(target)


def _copy_staged_database(staged_path: Path, live_path: Path) -> None:
    _copy_database(staged_path, live_path)


def _copy_database(source_path: Path, target_path: Path) -> None:
    if not source_path.is_file():
        raise RestoreFailedError("The source database for the copy is missing.")
    source = sqlite3.connect(source_path)
    try:
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _swap_documents(documents_dir: Path, rollback_documents: Path, staged_documents: Path) -> None:
    if documents_dir.exists():
        documents_dir.rename(rollback_documents)
    staged_documents.rename(documents_dir)


def _wipe_sessions(live_path: Path) -> None:
    connection = sqlite3.connect(live_path)
    try:
        connection.execute("DELETE FROM sessions")
        connection.commit()
    finally:
        connection.close()


def _health_check(live_path: Path, documents_dir: Path) -> None:
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    connection = sqlite3.connect(live_path)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchall()
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        admin_count = connection.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1"
        ).fetchone()[0]
        session_count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        storage_keys = [row[0] for row in connection.execute("SELECT storage_key FROM documents")]
    finally:
        connection.close()
    healthy = (
        quick_check == [("ok",)]
        and revisions == [(head,)]
        and admin_count >= 1
        and session_count == 0
        and all((documents_dir / key).is_file() for key in storage_keys)
    )
    if not healthy:
        raise RestoreFailedError("The restored data failed the health check.")


def _rollback(
    data_dir: Path,
    live_path: Path,
    *,
    operation_id: str,
    live_documents_existed: bool,
    code: str,
) -> None:
    operation_dir = data_dir / _RESTORE_STAGING_DIR / operation_id
    try:
        _restore_documents(
            data_dir / "documents",
            operation_dir,
            live_documents_existed=live_documents_existed,
        )
        _copy_database(operation_dir / "rollback" / "database.sqlite3", live_path)
        (data_dir / _JOURNAL_NAME).unlink()
    except Exception as error:
        logger.error("backup_restore_rollback_failed code=%s", type(error).__name__)
        raise RestoreRollbackFailedError(
            "The restore could not be rolled back; it will be retried at startup."
        ) from error
    _remove_operation_dir(operation_dir)
    logger.warning("backup_restore_rolled_back code=%s", code)


def _restore_documents(
    documents_dir: Path,
    operation_dir: Path,
    *,
    live_documents_existed: bool,
) -> None:
    rollback_documents = operation_dir / "rollback" / "documents"
    replaced_documents = operation_dir / "replaced-documents"
    if rollback_documents.exists():
        current_is_restored = documents_dir.exists()
    else:
        staged_documents = operation_dir / "staged" / "documents"
        current_is_restored = (
            not live_documents_existed and documents_dir.exists() and not staged_documents.exists()
        )
    if current_is_restored:
        if replaced_documents.exists():
            shutil.rmtree(replaced_documents)
        documents_dir.rename(replaced_documents)
    if rollback_documents.exists():
        rollback_documents.rename(documents_dir)


def _finish_committed(data_dir: Path, operation_dir: Path) -> None:
    try:
        if operation_dir.exists():
            shutil.rmtree(operation_dir)
        (data_dir / _JOURNAL_NAME).unlink(missing_ok=True)
    except OSError as error:
        logger.warning("backup_restore_cleanup_failed code=%s", type(error).__name__)


def _remove_operation_dir(operation_dir: Path) -> None:
    try:
        if operation_dir.exists():
            shutil.rmtree(operation_dir)
    except OSError as error:
        logger.warning("backup_restore_cleanup_failed code=%s", type(error).__name__)


def _operation_id(data_dir: Path, operation_dir: Path) -> str:
    staging_root = (data_dir / _RESTORE_STAGING_DIR).resolve()
    if operation_dir.resolve().parent != staging_root or not archive_format.is_storage_key(
        operation_dir.name
    ):
        raise ValueError("The path is not a restore operation directory.")
    return operation_dir.name


def _write_journal(
    data_dir: Path,
    *,
    operation_id: str,
    state: str,
    live_documents_existed: bool,
) -> None:
    journal = {
        "journal_version": _JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "live_documents_existed": live_documents_existed,
    }
    _write_atomic(data_dir / _JOURNAL_NAME, json.dumps(journal, sort_keys=True).encode("utf-8"))


def _read_journal(data_dir: Path) -> dict | None:
    try:
        raw = (data_dir / _JOURNAL_NAME).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RestoreFailedError("The restore journal could not be read.") from error
    try:
        journal = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RestoreFailedError("The restore journal is invalid.") from error
    valid = (
        isinstance(journal, dict)
        and set(journal) == _JOURNAL_KEYS
        and type(journal["journal_version"]) is int
        and journal["journal_version"] == _JOURNAL_VERSION
        and archive_format.is_storage_key(journal["operation_id"])
        and journal["state"] in _UNRESOLVED_STATES | {_COMMITTED_STATE}
        and isinstance(journal["live_documents_existed"], bool)
    )
    if not valid:
        raise RestoreFailedError("The restore journal is invalid.")
    return journal
