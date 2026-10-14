import asyncio
import time

from starlette.responses import HTMLResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from medical_archive.web.templates import templates

DRAIN_TIMEOUT_SECONDS = 30
RETRY_AFTER_SECONDS = 30
_POLL_INTERVAL_SECONDS = 0.05
_HEALTH_PATH = "/health"


class MaintenanceState:
    """Process-wide maintenance flag and in-flight request counter for restores."""

    def __init__(self) -> None:
        self.active = False
        self.in_flight = 0

    def enter(self) -> None:
        self.active = True

    def exit(self) -> None:
        self.active = False

    async def wait_until_drained(self, *, timeout_seconds: float) -> bool:
        """Wait until only the calling request is still in flight."""
        deadline = time.monotonic() + timeout_seconds
        while self.in_flight > 1:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        return True


def maintenance_page(*, status_code: int, rollback_failed: bool = False) -> HTMLResponse:
    content = templates.get_template("maintenance.html").render(rollback_failed=rollback_failed)
    headers = {"Cache-Control": "no-store"}
    if not rollback_failed:
        headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
    return HTMLResponse(content, status_code=status_code, headers=headers)


class MaintenanceMiddleware:
    def __init__(self, app: ASGIApp, *, state: MaintenanceState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.state.active and scope["path"] != _HEALTH_PATH:
            await maintenance_page(status_code=503)(scope, receive, send)
            return
        self.state.in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.state.in_flight -= 1


__all__ = [
    "DRAIN_TIMEOUT_SECONDS",
    "RETRY_AFTER_SECONDS",
    "MaintenanceMiddleware",
    "MaintenanceState",
    "maintenance_page",
]
