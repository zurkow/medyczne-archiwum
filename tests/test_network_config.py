from pathlib import Path

import pytest
from pydantic import ValidationError

from medical_archive.config import Settings

_SECRET = "s" * 32


@pytest.fixture
def tls_files(tmp_path: Path) -> tuple[Path, Path]:
    cert = tmp_path / "archiwum.crt"
    key = tmp_path / "archiwum.key"
    cert.write_text("synthetic certificate", encoding="utf-8")
    key.write_text("synthetic private key", encoding="utf-8")
    return cert, key


def test_production_config_refuses_public_bind_and_missing_tls(tmp_path: Path):
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            host="0.0.0.0",
            tls_cert=None,
            tls_key=None,
            data_dir=tmp_path,
        )

    message = str(error.value)
    assert "session_secret is required" in message
    assert "tls_cert must point to an existing certificate file" in message
    assert "tls_key must point to an existing private key file" in message
    assert "host must be an explicit private LAN address" in message


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:1234/v1",
        "http://lmstudio.example:1234/v1",
        "http://user:secret@127.0.0.1:1234/v1",
        "http://127.0.0.1:1234/v1?debug=1",
        "ftp://127.0.0.1:1234/v1",
    ],
)
def test_lm_studio_url_must_remain_loopback(tmp_path: Path, url: str):
    with pytest.raises(ValidationError) as error:
        Settings(testing=True, data_dir=tmp_path, lm_studio_base_url=url)

    assert "lm_studio_base_url must use a loopback host" in str(error.value)


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "::1",
        "224.0.0.1",
        "169.254.1.1",
        "fe80::1",
        "8.8.8.8",
        "100.64.0.1",
    ],
)
def test_production_rejects_every_non_private_lan_host_class(
    tmp_path: Path, tls_files: tuple[Path, Path], host: str
):
    cert, key = tls_files
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            host=host,
            tls_cert=cert,
            tls_key=key,
            session_secret=_SECRET,
            data_dir=tmp_path,
        )

    assert "host must be an explicit private LAN address" in str(error.value)


def test_production_accepts_private_lan_host_with_existing_tls_files(
    tmp_path: Path, tls_files: tuple[Path, Path]
):
    cert, key = tls_files

    settings = Settings(
        environment="production",
        host="192.168.1.10",
        tls_cert=cert,
        tls_key=key,
        session_secret=_SECRET,
        data_dir=tmp_path,
    )

    assert settings.environment == "production"
    assert settings.host == "192.168.1.10"
    assert settings.tls_cert == cert.resolve()
    assert settings.tls_key == key.resolve()


def test_development_rejects_non_loopback_host(tmp_path: Path):
    with pytest.raises(ValidationError) as error:
        Settings(testing=True, data_dir=tmp_path, host="192.168.1.10")

    assert "host must be a loopback address unless environment is production" in str(error.value)


@pytest.mark.parametrize("wildcard", ["*", "*.lan"])
def test_allowed_hosts_rejects_wildcards(tmp_path: Path, wildcard: str):
    with pytest.raises(ValidationError):
        Settings(testing=True, data_dir=tmp_path, allowed_hosts=[wildcard])

    settings = Settings(testing=True, data_dir=tmp_path, allowed_hosts=["Archiwum.LAN"])
    assert settings.allowed_hosts == ["archiwum.lan"]
