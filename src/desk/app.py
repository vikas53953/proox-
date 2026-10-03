"""HTTP surface: the WhatsApp webhook and an operator health check — nothing else.

There is deliberately no customer web UI, broker, order or trade route (RC01, E01).
"""

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from desk import SPEC_VERSION
from desk.config import Settings, load_settings
from desk.db.session import make_engine, make_session_factory
from desk.logsafe import install as install_log_redaction
from desk.pii import check_startup, configure_from
from desk.transport.whatsapp.webhook import router as whatsapp_router


def create_app(
    settings: Settings | None = None, session_factory: sessionmaker[Session] | None = None
) -> FastAPI:
    install_log_redaction()
    settings = settings or load_settings()
    configure_from(settings)  # B02: transport-id encryption (OFF unless enabled)
    if session_factory is None and settings.database_url:
        session_factory = make_session_factory(make_engine(settings.database_url))
    if session_factory is not None:
        with session_factory() as s:
            check_startup(s)  # refuse mixed plaintext / encrypted transport ids
    app = FastAPI(
        title="desk", version=SPEC_VERSION, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(whatsapp_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": "RESEARCH"}

    return app
