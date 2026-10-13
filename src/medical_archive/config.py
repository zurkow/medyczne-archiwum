import os
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEDICAL_ARCHIVE_", extra="ignore")

    session_secret: str | None = None
    data_dir: Path = Path.home() / ".medical-archive"
    database_url: str | None = None

    def __init__(self, *, testing: bool = False, **values: object) -> None:
        super().__init__(**values)
        if not testing and not self.session_secret:
            raise ValueError(
                "session_secret is required outside test mode; configure "
                "MEDICAL_ARCHIVE_SESSION_SECRET with at least 32 random bytes."
            )

    @field_validator("session_secret")
    @classmethod
    def validate_session_secret(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value.encode("utf-8")) < 32):
            raise ValueError("session_secret must contain at least 32 bytes.")
        return value

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"data_dir must be a writable directory: {path}") from error
        if not path.is_dir() or not os.access(path, os.W_OK):
            raise ValueError(f"data_dir must be a directory: {path}")
        return path

    @model_validator(mode="after")
    def validate_database_url(self) -> "Settings":
        if self.database_url is None:
            self.database_url = URL.create(
                "sqlite",
                database=(self.data_dir / "medical_archive.db").as_posix(),
            ).render_as_string(hide_password=False)
        try:
            url = make_url(self.database_url)
        except (TypeError, ValueError) as error:
            raise ValueError("database_url must be a valid SQLite URL.") from error
        if url.get_backend_name() != "sqlite" or not url.database:
            raise ValueError("database_url must be a SQLite URL with a database path.")
        return self
