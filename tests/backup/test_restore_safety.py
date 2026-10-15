import json
import logging
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from medical_archive.backup import service
from medical_archive.backup.crypto import InvalidBackup
from medical_archive.backup.service import (
    MissingAdministratorError,
    RestoreFailedError,
    UnsupportedBackupRevisionError,
    recover_backup_state,
)
from medical_archive.config import Settings
from medical_archive.main import create_app
from medical_archive.rag.index import load_sqlite_vec

PASSWORD = "synthetic backup password"
WRONG_PASSWORD = "synthetic wrong password"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JOURNAL_NAME = ".restore-journal.json"


class _SimulatedCrash(BaseException):
    pass


def _documents_listing(documents_dir: Path) -> dict[str, bytes]:
    if not documents_dir.exists():
        return {}
    return {path.name: path.read_bytes() for path in sorted(documents_dir.iterdir())}


def _database_dump(database_path: Path) -> list[str]:
    connection = sqlite3.connect(database_path)
    try:
        load_sqlite_vec(connection)
        return list(connection.iterdump())
    finally:
        connection.close()


def _query(database_path: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _upload(backup_service, envelope: bytes) -> Path:
    upload_path = backup_service.new_operation_dir() / "upload.marchive"
    upload_path.write_bytes(envelope)
    return upload_path


def _restorable_envelope(make_backup_database, make_envelope) -> tuple[bytes, str]:
    restored_key = str(uuid.uuid4())
    database_path = make_backup_database(
        (
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
            ("restored-admin@example.test", "synthetic"),
        ),
        ("UPDATE documents SET storage_key = ?", (restored_key,)),
    )
    envelope = make_envelope(
        PASSWORD,
        database_path=database_path,
        files={restored_key: b"synthetic restored original"},
    )
    return envelope, restored_key


def _live_copy_envelope(make_backup_database, make_envelope, seeded_store, *statements) -> bytes:
    return make_envelope(
        PASSWORD,
        database_path=make_backup_database(*statements),
        files={seeded_store.storage_key: seeded_store.original},
    )


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_failed_swap_rolls_back_database_and_documents(
    monkeypatch: pytest.MonkeyPatch,
    backup_service,
    backup_data_dir: Path,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    envelope, _ = _restorable_envelope(make_backup_database, make_envelope)
    staged = backup_service.stage_restore(_upload(backup_service, envelope), PASSWORD)
    dump_before = _database_dump(seeded_store.database_path)
    documents_before = _documents_listing(seeded_store.documents_dir)
    real_swap = service._swap_documents

    def swap_then_fail(*args, **kwargs):
        real_swap(*args, **kwargs)
        raise OSError("synthetic swap failure")

    monkeypatch.setattr(service, "_swap_documents", swap_then_fail)

    with pytest.raises(RestoreFailedError) as raised:
        backup_service.apply_restore(staged)

    assert type(raised.value) is RestoreFailedError
    assert _database_dump(seeded_store.database_path) == dump_before
    assert _documents_listing(seeded_store.documents_dir) == documents_before
    assert _query(seeded_store.database_path, "SELECT COUNT(*) FROM sessions") == [(1,)]
    assert not (backup_data_dir / _JOURNAL_NAME).exists()
    assert not staged.operation_dir.exists()


def test_interrupted_restore_is_rolled_back_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    backup_service,
    backup_data_dir: Path,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    envelope, _ = _restorable_envelope(make_backup_database, make_envelope)
    staged = backup_service.stage_restore(_upload(backup_service, envelope), PASSWORD)
    dump_before = _database_dump(seeded_store.database_path)
    documents_before = _documents_listing(seeded_store.documents_dir)
    real_swap = service._swap_documents

    def swap_then_crash(*args, **kwargs):
        real_swap(*args, **kwargs)
        raise _SimulatedCrash

    monkeypatch.setattr(service, "_swap_documents", swap_then_crash)
    with pytest.raises(_SimulatedCrash):
        backup_service.apply_restore(staged)
    assert (backup_data_dir / _JOURNAL_NAME).exists()
    assert _documents_listing(seeded_store.documents_dir) != documents_before
    stale_export = backup_data_dir / ".backup-tmp" / str(uuid.uuid4())
    stale_export.mkdir(parents=True)
    (stale_export / "snapshot.sqlite3").write_bytes(b"synthetic plaintext")

    recover_backup_state(
        data_dir=backup_data_dir,
        database_url=f"sqlite:///{seeded_store.database_path.as_posix()}",
    )

    assert _database_dump(seeded_store.database_path) == dump_before
    assert _documents_listing(seeded_store.documents_dir) == documents_before
    assert not (backup_data_dir / _JOURNAL_NAME).exists()
    assert list((backup_data_dir / ".restore-staging").iterdir()) == []
    assert list((backup_data_dir / ".backup-tmp").iterdir()) == []


@pytest.mark.parametrize(
    "case",
    [
        "corrupt_bytes",
        "wrong_password",
        "flipped_tag",
        "traversal_archive",
        "unsupported_envelope_version",
    ],
)
def test_bad_backup_never_replaces_existing_database(
    case: str,
    backup_service,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    password = PASSWORD
    if case == "corrupt_bytes":
        envelope = b"synthetic corrupt backup bytes" * 4
    elif case == "traversal_archive":
        envelope = make_envelope(
            PASSWORD,
            raw_entries={"manifest.json": b"{}", "../outside.txt": b"synthetic"},
        )
    else:
        envelope = bytearray(_live_copy_envelope(make_backup_database, make_envelope, seeded_store))
        if case == "wrong_password":
            password = WRONG_PASSWORD
        elif case == "flipped_tag":
            envelope[-1] ^= 0x01
        else:
            envelope[8] = 2
        envelope = bytes(envelope)
    database_before = seeded_store.database_path.read_bytes()
    documents_before = _documents_listing(seeded_store.documents_dir)
    upload_path = _upload(backup_service, envelope)

    with pytest.raises(InvalidBackup):
        backup_service.stage_restore(upload_path, password)
    backup_service.discard(upload_path.parent)

    assert seeded_store.database_path.read_bytes() == database_before
    assert _documents_listing(seeded_store.documents_dir) == documents_before
    assert not upload_path.parent.exists()


def test_restore_rejects_path_traversal_entry_before_database_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backup_service,
    seeded_store,
    make_envelope,
):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("The live database must not be replaced.")

    monkeypatch.setattr(service, "_copy_staged_database", fail_if_called)
    envelope = make_envelope(
        PASSWORD,
        raw_entries={
            "manifest.json": b"{}",
            "database.sqlite3": b"synthetic database",
            "files/../../outside.txt": b"synthetic",
        },
    )
    database_before = seeded_store.database_path.read_bytes()
    upload_path = _upload(backup_service, envelope)

    with pytest.raises(InvalidBackup):
        backup_service.stage_restore(upload_path, PASSWORD)

    assert not (upload_path.parent / "staged").exists()
    assert not list(tmp_path.rglob("outside.txt"))
    assert seeded_store.database_path.read_bytes() == database_before


def test_restore_rejects_backup_without_admin_account(
    backup_service,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    envelope = _live_copy_envelope(
        make_backup_database,
        make_envelope,
        seeded_store,
        ("UPDATE users SET is_admin = 0", ()),
    )
    database_before = seeded_store.database_path.read_bytes()

    with pytest.raises(MissingAdministratorError):
        backup_service.stage_restore(_upload(backup_service, envelope), PASSWORD)

    assert seeded_store.database_path.read_bytes() == database_before


def test_restore_rejects_newer_schema_revision(
    backup_service,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    envelope = _live_copy_envelope(
        make_backup_database,
        make_envelope,
        seeded_store,
        ("UPDATE alembic_version SET version_num = ?", ("9999_future",)),
    )
    database_before = seeded_store.database_path.read_bytes()

    with pytest.raises(UnsupportedBackupRevisionError):
        backup_service.stage_restore(_upload(backup_service, envelope), PASSWORD)

    assert seeded_store.database_path.read_bytes() == database_before


def test_restore_upgrades_older_revision_only_on_staged_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backup_service,
    seeded_store,
    make_envelope,
):
    old_database = tmp_path / "old-backup.sqlite3"
    config = _alembic_config(old_database)
    command.upgrade(config, "0004_vector_index")
    connection = sqlite3.connect(old_database)
    try:
        connection.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
            ("old-admin@example.test", "synthetic"),
        )
        connection.commit()
    finally:
        connection.close()
    head = ScriptDirectory.from_config(config).get_current_head()
    envelope = make_envelope(PASSWORD, database_path=old_database)
    monkeypatch.setenv(
        "MEDICAL_ARCHIVE_DATABASE_URL",
        f"sqlite:///{seeded_store.database_path.as_posix()}",
    )
    database_before = seeded_store.database_path.read_bytes()

    staged = backup_service.stage_restore(_upload(backup_service, envelope), PASSWORD)

    assert seeded_store.database_path.read_bytes() == database_before
    assert staged.schema_revision == head
    assert _query(staged.database_path, "SELECT version_num FROM alembic_version") == [(head,)]
    backup_service.discard(staged.operation_dir)


def test_restore_and_export_never_log_passwords(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    backup_service,
    backup_data_dir: Path,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    monkeypatch.setattr(service.logger, "disabled", False)
    caplog.set_level(logging.DEBUG)
    export_path = tmp_path / "export.marchive"
    backup_service.create_backup(export_path, PASSWORD, confirmed_all_profiles=True)
    failed_upload = _upload(backup_service, export_path.read_bytes())
    with pytest.raises(InvalidBackup):
        backup_service.stage_restore(failed_upload, WRONG_PASSWORD)
    backup_service.discard(failed_upload.parent)
    envelope, restored_key = _restorable_envelope(make_backup_database, make_envelope)

    staged = backup_service.stage_restore(_upload(backup_service, envelope), PASSWORD)
    backup_service.apply_restore(staged)

    assert _query(
        seeded_store.database_path,
        "SELECT COUNT(*) FROM users WHERE email = 'restored-admin@example.test'",
    ) == [(1,)]
    assert _query(seeded_store.database_path, "SELECT COUNT(*) FROM sessions") == [(0,)]
    assert _documents_listing(seeded_store.documents_dir) == {
        restored_key: b"synthetic restored original"
    }
    assert list((backup_data_dir / ".restore-staging").iterdir()) == []
    assert "backup_export_succeeded" in caplog.text
    assert "backup_restore_applied" in caplog.text
    for record in caplog.records:
        logged = f"{record.getMessage()} {record.args!r} {record.exc_text or ''}"
        assert PASSWORD not in logged
        assert WRONG_PASSWORD not in logged
    assert PASSWORD not in caplog.text
    assert WRONG_PASSWORD not in caplog.text


def _journal(operation_id: str, state: str, **extra) -> bytes:
    return json.dumps(
        {
            "journal_version": 1,
            "operation_id": operation_id,
            "state": state,
            "live_documents_existed": True,
            **extra,
        }
    ).encode("utf-8")


def test_startup_recovery_of_committed_restore_only_cleans_up_and_never_rolls_back(
    backup_service,
    backup_data_dir: Path,
    seeded_store,
    make_backup_database,
):
    operation_dir = backup_service.new_operation_dir()
    rollback_dir = operation_dir / "rollback"
    (rollback_dir / "documents").mkdir(parents=True)
    make_backup_database(("DELETE FROM documents", ())).replace(rollback_dir / "database.sqlite3")
    (backup_data_dir / _JOURNAL_NAME).write_bytes(_journal(operation_dir.name, "committed"))
    stale_upload = backup_service.new_operation_dir() / "upload.marchive"
    stale_upload.write_bytes(b"synthetic stale upload")
    stale_export = backup_data_dir / ".backup-tmp" / f"{uuid.uuid4()}.marchive"
    stale_export.parent.mkdir()
    stale_export.write_bytes(b"synthetic stale export")
    database_before = seeded_store.database_path.read_bytes()
    documents_before = _documents_listing(seeded_store.documents_dir)

    recover_backup_state(
        data_dir=backup_data_dir,
        database_url=f"sqlite:///{seeded_store.database_path.as_posix()}",
    )

    assert seeded_store.database_path.read_bytes() == database_before
    assert _documents_listing(seeded_store.documents_dir) == documents_before
    assert not (backup_data_dir / _JOURNAL_NAME).exists()
    assert list((backup_data_dir / ".restore-staging").iterdir()) == []
    assert list((backup_data_dir / ".backup-tmp").iterdir()) == []


@pytest.mark.parametrize(
    "journal",
    [
        b"{not json",
        b"\xff\xfe",
        _journal(str(uuid.uuid4()), "committed", path="C:/synthetic"),
        _journal(str(uuid.uuid4()), "rolling_forward"),
        _journal("../documents", "prepared"),
    ],
    ids=["not-json", "not-utf8", "extra-key", "unknown-state", "traversal-operation-id"],
)
def test_app_refuses_to_start_when_restore_journal_is_invalid(tmp_path: Path, journal: bytes):
    data_dir = tmp_path / "data"
    documents_dir = data_dir / "documents"
    documents_dir.mkdir(parents=True)
    (documents_dir / str(uuid.uuid4())).write_bytes(b"synthetic live original")
    stale_staging = data_dir / ".restore-staging" / str(uuid.uuid4())
    stale_staging.mkdir(parents=True)
    (data_dir / _JOURNAL_NAME).write_bytes(journal)
    documents_before = _documents_listing(documents_dir)
    settings = Settings(session_secret="s" * 32, data_dir=data_dir)

    with pytest.raises(RestoreFailedError):
        create_app(settings=settings)

    assert (data_dir / _JOURNAL_NAME).read_bytes() == journal
    assert _documents_listing(documents_dir) == documents_before
    assert stale_staging.exists()
