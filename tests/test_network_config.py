from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import medical_archive.__main__ as entrypoint
from medical_archive.config import Settings
from medical_archive.main import create_app
from medical_archive.preflight import Preflight, PreflightResult

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


@pytest.mark.parametrize(
    ("configured", "message"),
    [
        ("cert-only", "tls_cert and tls_key must be configured together"),
        ("key-only", "tls_cert and tls_key must be configured together"),
        ("missing-key-file", "tls_key must point to an existing private key file"),
    ],
)
def test_development_rejects_incomplete_tls_pair(
    tmp_path: Path, tls_files: tuple[Path, Path], configured: str, message: str
):
    cert, key = tls_files
    tls = {
        "cert-only": {"tls_cert": cert},
        "key-only": {"tls_key": key},
        "missing-key-file": {"tls_cert": cert, "tls_key": tmp_path / "missing.key"},
    }[configured]

    with pytest.raises(ValidationError, match=message):
        Settings(testing=True, data_dir=tmp_path, **tls)


@pytest.mark.parametrize("wildcard", ["*", "*.lan"])
def test_allowed_hosts_rejects_wildcards(tmp_path: Path, wildcard: str):
    with pytest.raises(ValidationError):
        Settings(testing=True, data_dir=tmp_path, allowed_hosts=[wildcard])

    settings = Settings(testing=True, data_dir=tmp_path, allowed_hosts=["Archiwum.LAN"])
    assert settings.allowed_hosts == ["archiwum.lan"]


def test_production_app_rejects_foreign_host_header_and_sends_no_cors_headers(
    tmp_path: Path, tls_files: tuple[Path, Path]
):
    cert, key = tls_files
    settings = Settings(
        environment="production",
        host="192.168.1.10",
        allowed_hosts=["archiwum.lan"],
        tls_cert=cert,
        tls_key=key,
        session_secret=_SECRET,
        data_dir=tmp_path,
    )
    client = TestClient(create_app(settings=settings), base_url="https://archiwum.lan")

    foreign = client.get("/health", headers={"host": "evil.example"})
    trusted = client.get("/health", headers={"origin": "https://evil.example"})

    assert foreign.status_code == 400
    assert trusted.status_code == 200
    assert "access-control-allow-origin" not in trusted.headers
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_create_app_outside_testing_refuses_to_start_without_session_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MEDICAL_ARCHIVE_SESSION_SECRET", raising=False)
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATA_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="session_secret is required"):
        create_app()


def test_serve_runs_uvicorn_with_tls_and_without_proxy_headers(
    tmp_path: Path, tls_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    cert, key = tls_files
    monkeypatch.setenv("MEDICAL_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("MEDICAL_ARCHIVE_HOST", "192.168.1.10")
    monkeypatch.setenv("MEDICAL_ARCHIVE_PORT", "8443")
    monkeypatch.setenv("MEDICAL_ARCHIVE_TLS_CERT", str(cert))
    monkeypatch.setenv("MEDICAL_ARCHIVE_TLS_KEY", str(key))
    monkeypatch.setenv("MEDICAL_ARCHIVE_SESSION_SECRET", _SECRET)
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATA_DIR", str(tmp_path))
    passing = PreflightResult(status="ok", checks=(), missing_components=())
    monkeypatch.setattr(Preflight, "check", lambda self: passing)
    calls = []
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **options: calls.append((app, options))
    )

    entrypoint.serve()

    assert len(calls) == 1
    _app, options = calls[0]
    assert options == {
        "host": "192.168.1.10",
        "port": 8443,
        "ssl_certfile": str(cert.resolve()),
        "ssl_keyfile": str(key.resolve()),
        "proxy_headers": False,
        "server_header": False,
    }
