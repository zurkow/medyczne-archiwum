import uvicorn

from medical_archive.config import Settings
from medical_archive.main import create_app


def serve() -> None:
    settings = Settings()
    app = create_app(settings=settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        ssl_certfile=None if settings.tls_cert is None else str(settings.tls_cert),
        ssl_keyfile=None if settings.tls_key is None else str(settings.tls_key),
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    serve()
