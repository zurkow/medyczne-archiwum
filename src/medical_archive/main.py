from fastapi import FastAPI

from medical_archive.auth.router import router as auth_router
from medical_archive.web.router import router as web_router


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(
        title="Medyczne Archiwum",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(auth_router)
    app.include_router(web_router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
