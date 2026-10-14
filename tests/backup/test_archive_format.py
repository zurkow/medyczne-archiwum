import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from medical_archive.backup import format as archive_format
from medical_archive.backup.crypto import InvalidBackup
from medical_archive.backup.format import DATABASE_NAME, MANIFEST_NAME, validate_archive
from medical_archive.backup.service import (
    BackupSourceError,
    BackupTooLargeError,
    create_backup,
)

PASSWORD = "synthetic backup password"
_DATABASE_BYTES = b"synthetic database bytes"
_FILE_BYTES = b"synthetic original file"

Entries = list[tuple[zipfile.ZipInfo, bytes]]
Mutation = Callable[[dict, Entries], None]


def _digest(data: bytes) -> dict:
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _entry(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo("placeholder")
    info.filename = name
    info.compress_type = zipfile.ZIP_DEFLATED
    return info, data


def _build_archive(mutate: Mutation | None = None) -> bytes:
    storage_key = str(uuid.uuid4())
    manifest = {
        "format_version": 1,
        "archive_id": str(uuid.uuid4()),
        "schema_revision": "test_schema_1",
        "database": _digest(_DATABASE_BYTES),
        "storage_keys": [storage_key],
        "files": {storage_key: _digest(_FILE_BYTES)},
    }
    entries = [
        _entry(DATABASE_NAME, _DATABASE_BYTES),
        _entry(f"files/{storage_key}", _FILE_BYTES),
    ]
    if mutate is not None:
        mutate(manifest, entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue()


def _add_entry(name: str) -> Mutation:
    def mutate(manifest: dict, entries: Entries) -> None:
        entries.append(_entry(name, b"synthetic"))

    return mutate


def _remove_listed_file(manifest: dict, entries: Entries) -> None:
    del entries[1]


def _mark_file_as_symlink(manifest: dict, entries: Entries) -> None:
    entries[1][0].external_attr = 0o120777 << 16


def _wrong_database_hash(manifest: dict, entries: Entries) -> None:
    manifest["database"]["sha256"] = hashlib.sha256(b"other").hexdigest()


def _wrong_file_size(manifest: dict, entries: Entries) -> None:
    storage_key = manifest["storage_keys"][0]
    manifest["files"][storage_key]["size"] += 1


@pytest.mark.parametrize(
    "mutate",
    [
        _add_entry("/etc/x"),
        _add_entry(f"files\\{uuid.uuid4()}"),
        _add_entry("C:/x"),
        _add_entry("files/not-a-uuid"),
        _add_entry(f"files/{uuid.uuid4()}"),
        _remove_listed_file,
        _mark_file_as_symlink,
    ],
    ids=[
        "absolute-path",
        "backslash-name",
        "drive-letter",
        "non-uuid-file",
        "unlisted-file",
        "missing-listed-file",
        "symlink-mode",
    ],
)
def test_archive_validation_rejects_unlisted_missing_and_symlink_entries(mutate: Mutation):
    with pytest.raises(InvalidBackup):
        validate_archive(_build_archive(mutate))


@pytest.mark.parametrize(
    "mutate",
    [_wrong_database_hash, _wrong_file_size],
    ids=["wrong-sha256", "wrong-size"],
)
def test_archive_validation_rejects_entry_whose_hash_or_size_differs_from_manifest(
    mutate: Mutation,
):
    assert validate_archive(_build_archive())["schema_revision"] == "test_schema_1"

    with pytest.raises(InvalidBackup):
        validate_archive(_build_archive(mutate))


def _create_store(data_dir: Path, *, with_original: bool) -> str:
    data_dir.mkdir()
    database_path = data_dir / "medical_archive.db"
    storage_key = str(uuid.uuid4())
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('test_schema_1')")
        connection.execute("CREATE TABLE documents (storage_key TEXT NOT NULL)")
        connection.execute("INSERT INTO documents VALUES (?)", (storage_key,))
        connection.commit()
    finally:
        connection.close()
    documents_dir = data_dir / "documents"
    documents_dir.mkdir()
    if with_original:
        (documents_dir / storage_key).write_bytes(_FILE_BYTES)
    return f"sqlite:///{database_path.as_posix()}"


def _backup_temp_entries(data_dir: Path) -> list[Path]:
    temp_dir = data_dir / ".backup-tmp"
    return list(temp_dir.iterdir()) if temp_dir.exists() else []


def test_backup_refuses_export_when_a_referenced_original_is_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    database_url = _create_store(data_dir, with_original=False)
    destination = tmp_path / "archive.marchive"

    with pytest.raises(BackupSourceError):
        create_backup(
            destination,
            PASSWORD,
            data_dir=data_dir,
            database_url=database_url,
            confirmed_all_profiles=True,
        )

    assert not destination.exists()
    assert _backup_temp_entries(data_dir) == []


def test_backup_refuses_export_over_size_limit_before_building_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_dir = tmp_path / "data"
    database_url = _create_store(data_dir, with_original=True)
    destination = tmp_path / "archive.marchive"

    def fail_if_called(*args: object, **kwargs: object) -> dict:
        pytest.fail("write_archive must not run when the estimate exceeds the limit")

    monkeypatch.setattr(archive_format, "write_archive", fail_if_called)

    with pytest.raises(BackupTooLargeError):
        create_backup(
            destination,
            PASSWORD,
            data_dir=data_dir,
            database_url=database_url,
            confirmed_all_profiles=True,
            max_bytes=1024,
        )

    assert not destination.exists()
    assert _backup_temp_entries(data_dir) == []
