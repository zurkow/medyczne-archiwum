import json
import io
import sqlite3
import uuid
import zipfile
from pathlib import Path

import pytest

from medical_archive.backup.crypto import InvalidBackup, decrypt
from medical_archive.backup.format import (
    DATABASE_NAME,
    MANIFEST_NAME,
    MAX_MANIFEST_BYTES,
    validate_archive,
)
from medical_archive.backup.service import BackupConfirmationRequired, create_backup


PASSWORD = "synthetic backup password"


def _create_synthetic_store(data_dir: Path) -> tuple[Path, str, str]:
    database_path = data_dir / "medical_archive.db"
    storage_key = str(uuid.uuid4())
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('test_schema_1')")
        connection.execute("CREATE TABLE documents (storage_key TEXT NOT NULL)")
        connection.execute("INSERT INTO documents VALUES (?)", (storage_key,))
        connection.execute("CREATE TABLE synthetic_records (value TEXT NOT NULL)")
        connection.commit()
        connection.execute("INSERT INTO synthetic_records VALUES ('committed in WAL')")
        connection.commit()
    except Exception:
        connection.close()
        raise

    documents_dir = data_dir / "documents"
    documents_dir.mkdir()
    (documents_dir / storage_key).write_bytes(b"synthetic original file")
    return database_path, storage_key, connection


def test_backup_roundtrip_includes_database_wal_and_original_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path, storage_key, live_connection = _create_synthetic_store(data_dir)
    destination = tmp_path / "archive.marchive"
    try:
        assert Path(f"{database_path}-wal").exists()
        create_backup(
            destination,
            PASSWORD,
            data_dir=data_dir,
            database_url=f"sqlite:///{database_path.as_posix()}",
            confirmed_all_profiles=True,
        )

        payload = decrypt(destination.read_bytes(), PASSWORD)
        manifest = validate_archive(payload)

        assert manifest["storage_keys"] == [storage_key]
        assert manifest["schema_revision"] == "test_schema_1"
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            restored_path = tmp_path / "restored.sqlite3"
            restored_path.write_bytes(archive.read(DATABASE_NAME))
            with sqlite3.connect(restored_path) as restored:
                assert restored.execute("SELECT value FROM synthetic_records").fetchone() == (
                    "committed in WAL",
                )
            assert archive.read(f"files/{storage_key}") == b"synthetic original file"
            raw_manifest = json.loads(archive.read(MANIFEST_NAME))
            assert raw_manifest["archive_id"]
            assert str(data_dir).encode() not in archive.read(MANIFEST_NAME)
    finally:
        live_connection.close()


def test_full_backup_requires_explicit_all_profile_confirmation(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path, _, live_connection = _create_synthetic_store(data_dir)
    try:
        with pytest.raises(BackupConfirmationRequired):
            create_backup(
                tmp_path / "archive.marchive",
                PASSWORD,
                data_dir=data_dir,
                database_url=f"sqlite:///{database_path.as_posix()}",
            )
    finally:
        live_connection.close()


def test_archive_validation_rejects_path_traversal_and_duplicate_entries():
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr(MANIFEST_NAME, b"{}")
        archive.writestr("../outside.txt", b"synthetic")

    with pytest.raises(InvalidBackup):
        validate_archive(archive_bytes.getvalue())


def test_archive_validation_rejects_oversized_manifest(monkeypatch: pytest.MonkeyPatch):
    storage_key = str(uuid.uuid4())
    manifest = {
        "archive_id": str(uuid.uuid4()),
        "format_version": 1,
        "schema_revision": "test_schema_1",
        "storage_keys": [storage_key],
    }
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        archive.writestr(DATABASE_NAME, b"synthetic database")
        archive.writestr(f"files/{storage_key}", b"synthetic file")
    monkeypatch.setattr(
        "medical_archive.backup.format.MAX_MANIFEST_BYTES",
        32,
    )

    with pytest.raises(InvalidBackup):
        validate_archive(archive_bytes.getvalue())
