import sys

import uvicorn

from medical_archive.config import Settings
from medical_archive.main import create_app
from medical_archive.preflight import Preflight


def serve() -> None:
    settings = Settings()
    result = Preflight(settings=settings).check()
    if result.missing_components:
        missing = ", ".join(result.missing_components)
        if result.status == "failed" and settings.environment == "production":
            print(f"Startup refused: preflight failed; missing {missing}.", file=sys.stderr)
            raise SystemExit(1)
        print(f"Warning: preflight {result.status}; missing {missing}.", file=sys.stderr)
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
