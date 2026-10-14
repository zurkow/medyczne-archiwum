import sqlite3
import uuid
from pathlib import Path

import pytest

from medical_archive.backup import restore

PASSWORD = "synthetic backup password"
_SECRET = "s" * 32


def _query(database_path: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _documents_listing(documents_dir: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(documents_dir.iterdir())}


def test_restore_cli_requires_typed_confirmation_and_restores_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
    auth_engine,
    backup_data_dir: Path,
    seeded_store,
    make_backup_database,
    make_envelope,
):
    restored_key = str(uuid.uuid4())
    database_path = make_backup_database(
        (
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
            ("restored-admin@example.test", "synthetic"),
        ),
        ("UPDATE documents SET storage_key = ?", (restored_key,)),
    )
    backup_file = tmp_path / "synthetic.marchive"
    backup_file.write_bytes(
        make_envelope(
            PASSWORD,
            database_path=database_path,
            files={restored_key: b"synthetic restored original"},
        )
    )
    auth_engine.dispose()
    monkeypatch.setenv("MEDICAL_ARCHIVE_SESSION_SECRET", _SECRET)
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATA_DIR", str(backup_data_dir))
    monkeypatch.setenv(
        "MEDICAL_ARCHIVE_DATABASE_URL", auth_engine.url.render_as_string(hide_password=False)
    )

    def unexpected_password_prompt(prompt: str = "") -> str:
        raise AssertionError("The backup password must not be requested before confirmation.")

    monkeypatch.setattr("builtins.input", lambda prompt="": "nope")
    monkeypatch.setattr(restore.getpass, "getpass", unexpected_password_prompt)

    with pytest.raises(SystemExit) as cancelled:
        restore.main(["--backup-file", str(backup_file)])

    assert cancelled.value.code == 1
    assert _documents_listing(seeded_store.documents_dir) == {
        seeded_store.storage_key: seeded_store.original
    }
    assert _query(seeded_store.database_path, "SELECT COUNT(*) FROM sessions") == [(1,)]
    assert backup_file.exists()

    monkeypatch.setattr("builtins.input", lambda prompt="": "RESTORE")
    monkeypatch.setattr(restore.getpass, "getpass", lambda prompt="": PASSWORD)

    restore.main(["--backup-file", str(backup_file)])

    assert "Restore completed. All sessions were signed out." in capsys.readouterr().out
    assert _documents_listing(seeded_store.documents_dir) == {
        restored_key: b"synthetic restored original"
    }
    assert _query(seeded_store.database_path, "SELECT COUNT(*) FROM sessions") == [(0,)]
    assert _query(
        seeded_store.database_path,
        "SELECT COUNT(*) FROM users WHERE email = 'restored-admin@example.test'",
    ) == [(1,)]
