from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from medical_archive.config import Settings


def test_migration_creates_identity_schema_in_sqlite(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())

    assert {"users", "sessions", "invitations", "access_grants"} <= names


def test_settings_rejects_empty_session_secret():
    try:
        Settings(session_secret="")
    except ValueError as error:
        assert "session_secret" in str(error)
    else:
        raise AssertionError("Empty session secret must be rejected")


@pytest.fixture
def migrated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "identity-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    monkeypatch.delenv("MEDICAL_ARCHIVE_DATABASE_URL", raising=False)
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()
