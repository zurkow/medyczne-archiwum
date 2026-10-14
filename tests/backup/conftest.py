"""Fixtures for backup and restore tests."""

import io
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from medical_archive.backup import crypto
from medical_archive.backup import format as archive_format
from medical_archive.backup.service import BackupService
from medical_archive.models import Document, LoginSession, User

_SMALL_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SeededStore:
    database_path: Path
    documents_dir: Path
    storage_key: str
    original: bytes


@pytest.fixture
def backup_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    (data_dir / "documents").mkdir(parents=True)
    return data_dir


@pytest.fixture
def backup_max_bytes() -> int:
    return _SMALL_MAX_BYTES


@pytest.fixture
def backup_service(backup_data_dir: Path, auth_engine, backup_max_bytes: int) -> BackupService:
    return BackupService(
        data_dir=backup_data_dir,
        database_url=auth_engine.url.render_as_string(hide_password=False),
        max_bytes=backup_max_bytes,
        dispose_engine=auth_engine.dispose,
    )


@pytest.fixture
def seeded_store(auth_engine, auth_session_factory, backup_data_dir: Path) -> SeededStore:
    storage_key = str(uuid.uuid4())
    original = b"synthetic live original"
    with auth_session_factory() as session:
        admin = User(email="live-admin@example.test", password_hash="synthetic", is_admin=True)
        session.add(admin)
        session.flush()
        session.add(
            Document(
                id=str(uuid.uuid4()),
                owner_user_id=admin.id,
                storage_key=storage_key,
                original_filename="synthetic.pdf",
                mime_type="application/pdf",
                byte_size=len(original),
            )
        )
        session.add(
            LoginSession(
                token_hash="a" * 64,
                user_id=admin.id,
                purpose="authenticated",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                csrf_token_hash="b" * 64,
            )
        )
        session.commit()
    documents_dir = backup_data_dir / "documents"
    (documents_dir / storage_key).write_bytes(original)
    return SeededStore(
        database_path=Path(auth_engine.url.database),
        documents_dir=documents_dir,
        storage_key=storage_key,
        original=original,
    )


@pytest.fixture
def make_backup_database(tmp_path: Path, seeded_store: SeededStore):
    def build(*statements: tuple[str, tuple]) -> Path:
        target = tmp_path / f"backup-source-{uuid.uuid4()}.sqlite3"
        source = sqlite3.connect(seeded_store.database_path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
            for sql, parameters in statements:
                destination.execute(sql, parameters)
            destination.commit()
        finally:
            destination.close()
            source.close()
        return target

    return build


@pytest.fixture
def make_envelope(tmp_path: Path):
    def build(password: str, **options) -> bytes:
        return _make_envelope(tmp_path / f"envelope-{uuid.uuid4()}", password, **options)

    return build


def _make_envelope(
    work_dir: Path,
    password: str,
    *,
    database_path: Path | None = None,
    files: dict[str, bytes] | None = None,
    raw_entries: dict[str, bytes] | None = None,
) -> bytes:
    if raw_entries is not None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in raw_entries.items():
                archive.writestr(name, content)
        return crypto.encrypt(buffer.getvalue(), password)

    if database_path is None:
        raise ValueError("A database is required for a valid backup envelope.")
    documents_dir = work_dir / "documents"
    documents_dir.mkdir(parents=True)
    for key, content in (files or {}).items():
        (documents_dir / key).write_bytes(content)
    connection = sqlite3.connect(database_path)
    try:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        storage_keys = [row[0] for row in connection.execute("SELECT storage_key FROM documents")]
    finally:
        connection.close()
    archive_path = work_dir / "archive.zip"
    archive_format.write_archive(
        archive_path,
        database_path=database_path,
        documents_dir=documents_dir,
        storage_keys=storage_keys,
        schema_revision=revision,
    )
    return crypto.encrypt(archive_path.read_bytes(), password)
