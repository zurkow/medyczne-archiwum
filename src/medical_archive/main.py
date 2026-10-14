from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from medical_archive.auth.router import router as auth_router
from medical_archive.backup.router import router as backup_router
from medical_archive.backup.service import BackupService, recover_backup_state
from medical_archive.config import Settings
from medical_archive.db import reset_database_engine
from medical_archive.documents.router import grant_router
from medical_archive.documents.router import router as documents_router
from medical_archive.measurements.router import grant_router as measurement_grant_router
from medical_archive.measurements.router import router as measurements_router
from medical_archive.rag.router import router as assistant_router
from medical_archive.web.router import router as web_router

_STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"


def create_app(*, testing: bool = False, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(testing=testing)
    app = FastAPI(
        title="Medyczne Archiwum",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.data_dir = settings.data_dir
    if not testing:
        recover_backup_state(data_dir=settings.data_dir, database_url=settings.database_url)
        app.state.backup_service = BackupService(
            data_dir=settings.data_dir,
            database_url=settings.database_url,
            max_bytes=settings.backup_max_bytes,
            dispose_engine=reset_database_engine,
        )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIRECTORY)), name="static")
    app.include_router(auth_router)
    app.include_router(documents_router)
    app.include_router(grant_router, prefix="/documents")
    app.include_router(measurements_router)
    app.include_router(measurement_grant_router, prefix="/measurements")
    app.include_router(assistant_router)
    app.include_router(backup_router)
    app.include_router(web_router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if settings.environment == "production":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[settings.host, *settings.allowed_hosts],
        )
    return app
