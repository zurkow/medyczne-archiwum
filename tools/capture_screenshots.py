"""Capture Playwright screenshots of the main pages using fictitious seed data.

The script starts a throwaway development server on loopback with a temporary
database and data directory, so it never touches real records.
"""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from playwright.sync_api import Page, sync_playwright
from sqlalchemy.orm import Session

from medical_archive.auth.service import create_first_admin
from medical_archive.db import create_database
from medical_archive.measurements.service import create_measurement
from medical_archive.weight.service import add_entry, set_height_cm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "correct horse battery staple"
DEFAULT_PAGES = (
    "/",
    "/documents/ui",
    "/measurements/ui",
    "/weight/ui",
    "/assistant/ui",
    "/auth/invite",
    "/settings/backup",
)
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 390, "height": 844},
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _prepare_database(database_url: str) -> None:
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    engine = create_database(database_url)
    try:
        with Session(engine) as session:
            admin = create_first_admin(session, email=ADMIN_EMAIL, password=ADMIN_PASSWORD)
            now = datetime.now(UTC)
            for day, (systolic, weight) in enumerate([(128, "81.4"), (124, "81.1"), (121, "80.6")]):
                measured_at = now - timedelta(days=14 - 7 * day)
                create_measurement(
                    session,
                    owner_user_id=admin.id,
                    name="Ciśnienie skurczowe",
                    value=Decimal(systolic),
                    unit="mmHg",
                    measured_at=measured_at,
                    note=None,
                )
                create_measurement(
                    session,
                    owner_user_id=admin.id,
                    name="Masa ciała",
                    value=Decimal(weight),
                    unit="kg",
                    measured_at=measured_at,
                    note=None,
                )
            set_height_cm(session, owner_user_id=admin.id, height_cm=Decimal(178))
            for offset, weight in enumerate(["88.2", "85.9", "83.4", "80.6", "77.8"]):
                add_entry(
                    session,
                    owner_user_id=admin.id,
                    weight_kg=Decimal(weight),
                    measured_on=(now - timedelta(days=28 - 7 * offset)).date(),
                )
    finally:
        engine.dispose()


def _wait_for_server(base_url: str, server: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"The server exited early with code {server.returncode}.")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("The server did not become healthy within 30 seconds.")


def _file_name(viewport: str, path: str) -> str:
    slug = path.strip("/").replace("/", "-") or "dashboard"
    return f"{viewport}-{slug}.png"


def _log_in(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/auth/login")
    page.fill("#email", ADMIN_EMAIL)
    page.fill("#password", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{base_url}/")


def _capture(base_url: str, pages: list[str], output_dir: Path) -> list[Path]:
    captured: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(viewport=viewport, locale="pl-PL")
                page = context.new_page()
                page.goto(f"{base_url}/auth/login")
                login_shot = output_dir / _file_name(viewport_name, "/auth/login")
                page.screenshot(path=login_shot, full_page=True)
                captured.append(login_shot)
                _log_in(page, base_url)
                for path in pages:
                    response = page.goto(f"{base_url}{path}")
                    if response is None or not response.ok:
                        status = "no response" if response is None else response.status
                        raise RuntimeError(f"GET {path} failed: {status}.")
                    shot = output_dir / _file_name(viewport_name, path)
                    page.screenshot(path=shot, full_page=True)
                    captured.append(shot)
                context.close()
        finally:
            browser.close()
    return captured


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("screenshots"))
    parser.add_argument(
        "--page",
        action="append",
        dest="pages",
        help="Additional path to capture after logging in (repeatable).",
    )
    options = parser.parse_args(arguments)
    pages = list(DEFAULT_PAGES) + [
        path for path in options.pages or [] if path not in DEFAULT_PAGES
    ]
    options.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="medical-archive-screens-") as workspace:
        workspace_path = Path(workspace)
        database_url = f"sqlite:///{(workspace_path / 'archive.db').as_posix()}"
        _prepare_database(database_url)

        port = _free_port()
        environment = {
            **os.environ,
            "MEDICAL_ARCHIVE_ENVIRONMENT": "development",
            "MEDICAL_ARCHIVE_HOST": "127.0.0.1",
            "MEDICAL_ARCHIVE_PORT": str(port),
            "MEDICAL_ARCHIVE_DATA_DIR": str(workspace_path / "data"),
            "MEDICAL_ARCHIVE_DATABASE_URL": database_url,
            "MEDICAL_ARCHIVE_SESSION_SECRET": secrets.token_urlsafe(32),
        }
        server = subprocess.Popen(
            [sys.executable, "-m", "medical_archive"],
            cwd=PROJECT_ROOT,
            env=environment,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            _wait_for_server(base_url, server)
            captured = _capture(base_url, pages, options.output)
        finally:
            server.terminate()
            server.wait(timeout=10)

    for shot in captured:
        print(shot.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
