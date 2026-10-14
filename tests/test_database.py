from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from medical_archive import db
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


def test_settings_default_to_configured_local_model_ids(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MEDICAL_ARCHIVE_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("MEDICAL_ARCHIVE_CHAT_MODEL", raising=False)

    settings = Settings(testing=True)

    assert settings.embedding_model == "text-embedding-nomic-embed-text-v1.5"
    assert settings.chat_model == "bielik-1.5b-v3.0-instruct"


def test_reset_database_engine_disposes_cached_engine_and_rebuilds_on_next_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    database_url = f"sqlite:///{(tmp_path / 'reset-test.db').as_posix()}"
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATABASE_URL", database_url)
    monkeypatch.setenv("MEDICAL_ARCHIVE_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATA_DIR", str(tmp_path))
    db.reset_database_engine()
    try:
        first_engine = db._database_engine()
        with first_engine.connect():
            pass
        assert first_engine.pool.checkedin() == 1

        db.reset_database_engine()

        assert first_engine.pool.checkedin() == 0
        second_engine = db._database_engine()
        assert second_engine is not first_engine
        sessions = db.get_session()
        assert next(sessions).get_bind() is second_engine
        sessions.close()
    finally:
        db.reset_database_engine()


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
