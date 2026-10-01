"""Settings read from environment variables only. No secret ever lives in source.

While gates G01-G06 are BLOCKED, the only allowed mode is "mock". Any attempt to run
with a real adapter fails at startup instead of silently choosing a provider (RC12).
Secret fields are excluded from repr so they cannot leak into logs (RC08).
"""

import os
from dataclasses import dataclass, field

ALLOWED_MODES = frozenset({"mock"})
GRAPH_API_VERSION = "v26.0"  # pinned in Technical spec v1.3; not configurable


class GateBlockedError(RuntimeError):
    """Raised when config asks for something an open gate (G01-G06) has not cleared."""


@dataclass(frozen=True)
class WhatsAppSettings:
    """Cloud API identity + webhook secrets (G02 BLOCKED: empty in every real env today)."""

    phone_number_id: str = ""
    waba_id: str = ""
    app_secret: str = field(default="", repr=False)
    verify_token: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)

    @property
    def webhook_ready(self) -> bool:
        return all((self.phone_number_id, self.waba_id, self.app_secret, self.verify_token))


@dataclass(frozen=True)
class Settings:
    mode: str
    database_url: str | None = field(repr=False)  # URL may embed a password
    model_adapter: str
    feed_adapter: str
    whatsapp: WhatsAppSettings = field(default_factory=WhatsAppSettings)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    mode = env.get("DESK_MODE", "mock")
    if mode not in ALLOWED_MODES:
        raise GateBlockedError(f"DESK_MODE={mode!r} not allowed: gates G01-G06 are BLOCKED")
    model_adapter = env.get("DESK_MODEL_ADAPTER", "mock")
    if model_adapter != "mock":
        raise GateBlockedError("model adapter BLOCKED by G01")
    feed_adapter = env.get("DESK_FEED_ADAPTER", "fixture")
    if feed_adapter != "fixture":
        raise GateBlockedError("feed adapter BLOCKED by G03")
    version = env.get("WHATSAPP_GRAPH_VERSION", GRAPH_API_VERSION)
    if version != GRAPH_API_VERSION:
        raise GateBlockedError(f"Graph API version is pinned to {GRAPH_API_VERSION}")
    return Settings(
        mode=mode,
        database_url=env.get("DESK_DATABASE_URL"),
        model_adapter=model_adapter,
        feed_adapter=feed_adapter,
        whatsapp=WhatsAppSettings(
            phone_number_id=env.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            waba_id=env.get("WHATSAPP_WABA_ID", ""),
            app_secret=env.get("WHATSAPP_APP_SECRET", ""),
            verify_token=env.get("WHATSAPP_VERIFY_TOKEN", ""),
            access_token=env.get("WHATSAPP_ACCESS_TOKEN", ""),
        ),
    )
