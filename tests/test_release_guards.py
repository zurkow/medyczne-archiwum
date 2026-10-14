from pathlib import Path

import pytest
from pydantic import ValidationError

import medical_archive.__main__ as entrypoint
from medical_archive import preflight
from medical_archive.config import Settings
from medical_archive.preflight import Preflight, PreflightCheck, PreflightResult
from medical_archive.rag.index import VectorExtensionUnavailableError

_SECRET = "s" * 64


@pytest.fixture
def settings_factory(tmp_path: Path):
    def build(**overrides) -> Settings:
        return Settings(data_dir=tmp_path, **overrides)

    return build


@pytest.fixture
def testing_settings(tmp_path: Path, auth_engine) -> Settings:
    return Settings(
        testing=True,
        data_dir=tmp_path,
        database_url=auth_engine.url.render_as_string(hide_password=False),
    )


@pytest.fixture
def ai_preflight(testing_settings: Settings) -> Preflight:
    return Preflight(settings=testing_settings, fetch_model_ids=list)


def test_production_start_refuses_missing_tls_configuration(settings_factory):
    with pytest.raises(ValidationError, match="tls_cert"):
        settings_factory(
            environment="production",
            host="192.168.1.10",
            session_secret="s" * 64,
            tls_cert=None,
            tls_key=None,
        )


def test_preflight_reports_missing_local_ai_without_hiding_the_degraded_state(ai_preflight):
    result = ai_preflight.check()

    assert result.status == "degraded"
    assert "embedding" in result.missing_components
    assert "generation" in result.missing_components


def test_preflight_fails_when_sqlite_vec_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, testing_settings: Settings
):
    def unavailable(connection) -> None:
        raise VectorExtensionUnavailableError("synthetic missing extension")

    monkeypatch.setattr(preflight, "load_sqlite_vec", unavailable)

    result = Preflight(settings=testing_settings, fetch_model_ids=list).check()

    assert result.status == "failed"
    assert "sqlite-vec" in result.missing_components


def test_preflight_main_strict_mode_fails_on_degraded(
    monkeypatch: pytest.MonkeyPatch, testing_settings: Settings, capsys
):
    def lm_studio_down(*, base_url: str, timeout_seconds: float) -> list[str]:
        raise OSError("synthetic connection refused")

    monkeypatch.setattr(preflight, "_fetch_lm_studio_model_ids", lm_studio_down)
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATA_DIR", str(testing_settings.data_dir))
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATABASE_URL", testing_settings.database_url)

    assert preflight.main([]) == 0
    assert preflight.main(["--strict"]) == 1
    output = capsys.readouterr().out
    assert "MISSING: embedding" in output
    assert "degraded" in output


def test_serve_refuses_production_start_when_preflight_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    cert = tmp_path / "archiwum.crt"
    key = tmp_path / "archiwum.key"
    cert.write_text("synthetic certificate", encoding="utf-8")
    key.write_text("synthetic private key", encoding="utf-8")
    monkeypatch.setenv("MEDICAL_ARCHIVE_ENVIRONMENT", "production")
    monkeypatch.setenv("MEDICAL_ARCHIVE_HOST", "192.168.1.10")
    monkeypatch.setenv("MEDICAL_ARCHIVE_TLS_CERT", str(cert))
    monkeypatch.setenv("MEDICAL_ARCHIVE_TLS_KEY", str(key))
    monkeypatch.setenv("MEDICAL_ARCHIVE_SESSION_SECRET", _SECRET)
    monkeypatch.setenv("MEDICAL_ARCHIVE_DATA_DIR", str(tmp_path))
    failed = PreflightResult(
        status="failed",
        checks=(
            PreflightCheck(
                component="sqlite-vec", ok=False, critical=True, detail="synthetic failure"
            ),
        ),
        missing_components=("sqlite-vec",),
    )
    monkeypatch.setattr(Preflight, "check", lambda self: failed)
    calls = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda app, **options: calls.append(app))

    with pytest.raises(SystemExit) as raised:
        entrypoint.serve()

    assert raised.value.code == 1
    assert calls == []
    captured = capsys.readouterr()
    assert "sqlite-vec" in captured.out + captured.err
