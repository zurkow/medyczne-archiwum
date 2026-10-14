"""Check the local runtime prerequisites without sending any document data."""

import argparse
import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.request import HTTPRedirectHandler, build_opener

from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from medical_archive.config import Settings
from medical_archive.rag.index import VectorExtensionUnavailableError, load_sqlite_vec

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_TESSERACT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class PreflightCheck:
    component: str
    ok: bool
    critical: bool
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    status: Literal["ok", "degraded", "failed"]
    checks: tuple[PreflightCheck, ...]
    missing_components: tuple[str, ...]


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _fetch_lm_studio_model_ids(*, base_url: str, timeout_seconds: float) -> list[str]:
    endpoint = f"{base_url.rstrip('/')}/models"
    with build_opener(_NoRedirectHandler).open(endpoint, timeout=timeout_seconds) as response:
        payload = json.load(response)
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        # ValueError is the fetcher contract for a malformed LM Studio response.
        raise ValueError("LM Studio returned an unexpected model list.")  # noqa: TRY004
    return [
        model["id"]
        for model in models
        if isinstance(model, dict) and isinstance(model.get("id"), str)
    ]


class Preflight:
    def __init__(
        self,
        *,
        settings: Settings,
        fetch_model_ids: Callable[[], list[str]] | None = None,
    ) -> None:
        self._settings = settings
        self._fetch_model_ids = fetch_model_ids or self._fetch_from_lm_studio

    def check(self) -> PreflightResult:
        checks = [
            self._check_python(),
            self._check_sqlite_vec(),
            self._check_database(),
            self._check_data_dir(),
        ]
        if self._settings.environment == "production":
            checks.append(self._check_tls())
        checks.append(self._check_pymupdf())
        checks.extend(self._check_ocr())
        checks.extend(self._check_models())

        missing = tuple(check.component for check in checks if not check.ok)
        if any(check.critical and not check.ok for check in checks):
            status = "failed"
        elif missing:
            status = "degraded"
        else:
            status = "ok"
        return PreflightResult(status=status, checks=tuple(checks), missing_components=missing)

    def _fetch_from_lm_studio(self) -> list[str]:
        return _fetch_lm_studio_model_ids(
            base_url=self._settings.lm_studio_base_url,
            timeout_seconds=self._settings.embedding_timeout_seconds,
        )

    def _check_python(self) -> PreflightCheck:
        version = sys.version_info
        return PreflightCheck(
            component="python",
            ok=version[:2] == (3, 12),
            critical=True,
            detail=f"detected {version.major}.{version.minor}.{version.micro}; 3.12 is required",
        )

    def _check_sqlite_vec(self) -> PreflightCheck:
        connection = sqlite3.connect(":memory:")
        try:
            load_sqlite_vec(connection)
        except VectorExtensionUnavailableError:
            return _missing("sqlite-vec", critical=True, detail="the extension could not load")
        finally:
            connection.close()
        return _available("sqlite-vec", critical=True, detail="extension loaded in SQLite")

    def _check_database(self) -> PreflightCheck:
        database = make_url(self._settings.database_url).database
        if not database or not Path(database).is_file():
            return _missing(
                "database", critical=True, detail="database file not found; run alembic upgrade"
            )
        head = ScriptDirectory(str(_MIGRATIONS_DIR)).get_current_head()
        revision = _read_revision(Path(database))
        if revision != head:
            return _missing(
                "database",
                critical=True,
                detail=f"schema revision {revision} is not head {head}; run alembic upgrade head",
            )
        return _available("database", critical=True, detail=f"schema revision {head}")

    def _check_data_dir(self) -> PreflightCheck:
        try:
            with tempfile.TemporaryFile(dir=self._settings.data_dir):
                pass
        except OSError as error:
            return _missing(
                "data_dir", critical=True, detail=f"not writable ({type(error).__name__})"
            )
        return _available("data_dir", critical=True, detail="writable")

    def _check_tls(self) -> PreflightCheck:
        for path in (self._settings.tls_cert, self._settings.tls_key):
            try:
                with open(path, "rb") as tls_file:
                    tls_file.read(1)
            except OSError as error:
                return _missing(
                    "tls",
                    critical=True,
                    detail=f"certificate or key is not readable ({type(error).__name__})",
                )
        return _available("tls", critical=True, detail="certificate and key are readable")

    def _check_pymupdf(self) -> PreflightCheck:
        try:
            importlib.import_module("pymupdf")
        except ImportError:
            return _missing(
                "pymupdf",
                critical=False,
                detail='install with `python -m pip install -e ".[local-ai]"`',
            )
        return _available("pymupdf", critical=False, detail="importable")

    def _check_ocr(self) -> list[PreflightCheck]:
        executable = shutil.which("tesseract")
        if executable is None:
            return [
                _missing("ocr", critical=False, detail="tesseract is not on PATH"),
                _missing("ocr-pol", critical=False, detail="cannot check without tesseract"),
            ]
        try:
            result = subprocess.run(
                [executable, "--list-langs"],
                capture_output=True,
                check=False,
                text=True,
                timeout=_TESSERACT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return [
                _available("ocr", critical=False, detail="tesseract is on PATH"),
                _missing(
                    "ocr-pol",
                    critical=False,
                    detail=f"could not run --list-langs ({type(error).__name__})",
                ),
            ]
        languages = {line.strip() for line in result.stdout.splitlines()}
        if result.returncode != 0 or "pol" not in languages:
            pol_check = _missing(
                "ocr-pol", critical=False, detail="pol is not listed; install pol.traineddata"
            )
        else:
            pol_check = _available("ocr-pol", critical=False, detail="pol is installed")
        return [_available("ocr", critical=False, detail="tesseract is on PATH"), pol_check]

    def _check_models(self) -> list[PreflightCheck]:
        try:
            model_ids = set(self._fetch_model_ids())
        except (OSError, ValueError) as error:
            detail = f"LM Studio is unavailable on loopback ({type(error).__name__})"
            return [
                _missing("embedding", critical=False, detail=detail),
                _missing("generation", critical=False, detail=detail),
            ]
        return [
            _model_check("embedding", self._settings.embedding_model, model_ids),
            _model_check("generation", self._settings.chat_model, model_ids),
        ]


def _available(component: str, *, critical: bool, detail: str) -> PreflightCheck:
    return PreflightCheck(component=component, ok=True, critical=critical, detail=detail)


def _missing(component: str, *, critical: bool, detail: str) -> PreflightCheck:
    return PreflightCheck(component=component, ok=False, critical=critical, detail=detail)


def _model_check(component: str, model_id: str, model_ids: set[str]) -> PreflightCheck:
    if model_id in model_ids:
        return _available(component, critical=False, detail=f"{model_id} is loaded")
    return _missing(component, critical=False, detail=f"{model_id} is not loaded in LM Studio")


def _read_revision(database_path: Path) -> str | None:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return rows[0][0] if len(rows) == 1 else None


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the local prerequisites of Medyczne Archiwum."
    )
    parser.add_argument(
        "--strict", action="store_true", help="Also fail when optional components are missing."
    )
    options = parser.parse_args(arguments)

    result = Preflight(settings=Settings(testing=True)).check()
    for check in result.checks:
        print(f"{'OK' if check.ok else 'MISSING'}: {check.component}: {check.detail}")
    print(f"Preflight status: {result.status}")
    if result.status == "failed" or (options.strict and result.status == "degraded"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
