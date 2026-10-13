from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from medical_archive.auth.router import router as auth_router
from medical_archive.config import Settings
from medical_archive.documents.router import grant_router
from medical_archive.documents.router import router as documents_router
from medical_archive.web.router import router as web_router

_STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(
        title="Medyczne Archiwum",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.data_dir = Settings(testing=True).data_dir
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIRECTORY)), name="static")
    app.include_router(auth_router)
    app.include_router(documents_router)
    app.include_router(grant_router, prefix="/documents")
    app.include_router(web_router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
