import ipaddress
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_DNS_LABEL = re.compile(r"^[a-z0-9-]{1,63}$")
_MAX_HOSTNAME_LENGTH = 253


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEDICAL_ARCHIVE_", extra="ignore")

    session_secret: str | None = None
    data_dir: Path = Path.home() / ".medical-archive"
    database_url: str | None = None
    lm_studio_base_url: str = "http://127.0.0.1:1234/v1"
    embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    chat_model: str = "bielik-1.5b-v3.0-instruct"
    embedding_timeout_seconds: float = Field(default=15.0, gt=0)
    generation_timeout_seconds: float = Field(default=90.0, gt=0)
    retrieval_relevance_threshold: float = Field(default=0.2, ge=0, le=1)
    retrieval_top_k: int = Field(default=8, gt=0, le=100)
    environment: Literal["development", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    tls_cert: Path | None = None
    tls_key: Path | None = None
    allowed_hosts: list[str] = []
    backup_max_bytes: int = Field(default=_GIB, ge=_MIB, le=_GIB)

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

    @field_validator("embedding_model")
    @classmethod
    def validate_embedding_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("embedding_model must name a local embedding model.")
        return value

    @field_validator("chat_model")
    @classmethod
    def validate_chat_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("chat_model must name a local chat model.")
        return value

    @field_validator("lm_studio_base_url")
    @classmethod
    def validate_lm_studio_base_url(cls, value: str) -> str:
        message = "lm_studio_base_url must use a loopback host (127.0.0.1, localhost or ::1)."
        try:
            parsed_url = urlsplit(value)
        except ValueError as error:
            raise ValueError(message) from error
        if (
            parsed_url.scheme not in {"http", "https"}
            or parsed_url.hostname not in _LOOPBACK_HOSTNAMES
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(message)
        return value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as error:
            raise ValueError("host must be an IP address, not a hostname.") from error

    @field_validator("tls_cert", "tls_key")
    @classmethod
    def resolve_tls_path(cls, value: Path | None) -> Path | None:
        return None if value is None else value.expanduser().resolve()

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            host = value.lower()
            if "*" in host:
                raise ValueError("allowed_hosts must not contain wildcards.")
            if not _is_ip_literal(host) and not _is_dns_hostname(host):
                raise ValueError("allowed_hosts entries must be DNS hostnames or IP addresses.")
            normalized.append(host)
        return normalized

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

    @model_validator(mode="after")
    def validate_network_profile(self) -> "Settings":
        if self.environment == "production":
            problems = []
            if self.session_secret is None:
                problems.append("session_secret is required")
            if self.tls_cert is None or not self.tls_cert.is_file():
                problems.append("tls_cert must point to an existing certificate file")
            if self.tls_key is None or not self.tls_key.is_file():
                problems.append("tls_key must point to an existing private key file")
            if not _is_private_lan_address(self.host):
                problems.append("host must be an explicit private LAN address")
            if problems:
                raise ValueError("Invalid production configuration: " + "; ".join(problems))
            return self
        if (self.tls_cert is None) != (self.tls_key is None):
            raise ValueError("tls_cert and tls_key must be configured together.")
        if self.tls_cert is not None and not self.tls_cert.is_file():
            raise ValueError("tls_cert must point to an existing certificate file.")
        if self.tls_key is not None and not self.tls_key.is_file():
            raise ValueError("tls_key must point to an existing private key file.")
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("host must be a loopback address unless environment is production.")
        return self


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_dns_hostname(value: str) -> bool:
    return len(value) <= _MAX_HOSTNAME_LENGTH and all(
        _DNS_LABEL.fullmatch(label) for label in value.split(".")
    )


def _is_private_lan_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_private and not (
        address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address.is_link_local
        or address.is_reserved
    )
