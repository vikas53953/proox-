"""HTTP surface. Only the WhatsApp webhook (added in M2) and an operator health check.

There is deliberately no customer web UI, broker, order or trade route (RC01, E01).
"""

from fastapi import FastAPI

from desk import SPEC_VERSION


def create_app() -> FastAPI:
    app = FastAPI(
        title="desk", version=SPEC_VERSION, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": "RESEARCH"}

    return app
