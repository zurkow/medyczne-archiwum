from fastapi import FastAPI


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(title="Medyczne Archiwum", docs_url=None, redoc_url=None)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
