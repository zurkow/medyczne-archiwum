"""Check local OCR and LM Studio prerequisites without sending document content."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, build_opener

failures: list[str] = []


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def report(requirement: str, available: bool, detail: str) -> None:
    status = "OK" if available else "MISSING"
    print(f"{status}: {requirement}: {detail}")
    if not available:
        failures.append(requirement)


def check_python_and_sqlite() -> None:
    python_supported = (3, 12) <= sys.version_info[:2] < (3, 13)
    report(
        "Python 3.12",
        python_supported,
        f"detected {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    report("SQLite", True, f"library version {sqlite3.sqlite_version}")


def check_sqlite_vec() -> None:
    try:
        import sqlite_vec
    except ImportError:
        report(
            "sqlite-vec",
            False,
            'install with `python -m pip install -e ".[local-ai]"`',
        )
        return

    connection = sqlite3.connect(":memory:")
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        version = importlib.metadata.version("sqlite-vec")
    except (sqlite3.Error, importlib.metadata.PackageNotFoundError) as error:
        report("sqlite-vec", False, f"could not load extension ({type(error).__name__})")
    else:
        report("sqlite-vec", True, f"version {version}; extension loaded in SQLite")
    finally:
        connection.close()


def check_pymupdf() -> None:
    try:
        import pymupdf
    except ImportError:
        report(
            "PyMuPDF",
            False,
            'install with `python -m pip install -e ".[local-ai]"`',
        )
        return

    report("PyMuPDF", True, f"version {pymupdf.VersionBind}")


def check_tesseract() -> None:
    configured_command = os.environ.get("TESSERACT_CMD", "tesseract")
    executable = shutil.which(configured_command)
    if executable is None and os.path.isfile(configured_command):
        executable = configured_command
    if executable is None:
        report(
            "Tesseract executable",
            False,
            "not found on PATH; install Tesseract or set TESSERACT_CMD to its executable",
        )
        report(
            "Tesseract Polish language data (pol.traineddata)",
            False,
            "cannot check until Tesseract is installed",
        )
        return

    try:
        result = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        report(
            "Tesseract executable",
            False,
            f"could not run --list-langs ({type(error).__name__})",
        )
        report(
            "Tesseract Polish language data (pol.traineddata)",
            False,
            "cannot check because Tesseract did not run successfully",
        )
        return

    if result.returncode != 0:
        report(
            "Tesseract executable",
            False,
            f"--list-langs exited with code {result.returncode}; check TESSDATA_PREFIX",
        )
        report(
            "Tesseract Polish language data (pol.traineddata)",
            False,
            "cannot confirm because Tesseract could not list languages",
        )
        return

    languages = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.strip().startswith("List of available")
    }
    report("Tesseract executable", True, "available")
    report(
        "Tesseract Polish language data (pol.traineddata)",
        "pol" in languages,
        "installed" if "pol" in languages else "not listed; install pol.traineddata",
    )


def check_lm_studio() -> None:
    raw_port = os.environ.get("LM_STUDIO_PORT", "1234")
    try:
        port = int(raw_port)
    except ValueError:
        report("LM Studio local API", False, "LM_STUDIO_PORT must be an integer from 1 to 65535")
        return
    if not 1 <= port <= 65535:
        report("LM Studio local API", False, "LM_STUDIO_PORT must be from 1 to 65535")
        return

    endpoint = f"http://127.0.0.1:{port}/v1/models"
    try:
        with build_opener(NoRedirectHandler).open(endpoint, timeout=3) as response:
            payload = json.load(response)
    except HTTPError as error:
        report(
            "LM Studio local API",
            False,
            f"loopback /v1/models returned HTTP {error.code}; start its local server",
        )
        return
    except (URLError, TimeoutError, OSError):
        report(
            "LM Studio local API",
            False,
            f"not reachable at 127.0.0.1:{port}; start the local server",
        )
        return
    except (json.JSONDecodeError, UnicodeDecodeError):
        report("LM Studio local API", False, "loopback /v1/models returned invalid JSON")
        return

    models = payload.get("data") if isinstance(payload, dict) else None
    model_names = (
        [
            model.get("id")
            for model in models
            if isinstance(model, dict) and isinstance(model.get("id"), str)
        ]
        if isinstance(models, list)
        else []
    )
    if not model_names:
        report(
            "LM Studio models",
            False,
            "local API is reachable but advertises no model IDs; load a model in LM Studio",
        )
        return

    report("LM Studio local API", True, f"reachable at 127.0.0.1:{port}/v1/models")
    report("LM Studio models", True, ", ".join(model_names))


def main() -> int:
    check_python_and_sqlite()
    check_sqlite_vec()
    check_pymupdf()
    check_tesseract()
    check_lm_studio()

    if failures:
        print(f"Local AI setup is incomplete: {', '.join(failures)}.")
        return 1

    print("All checked local AI requirements are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
