"""Settings read from environment variables only. No secret ever lives in source.

While gates G01-G06 are BLOCKED, the only allowed mode is "mock". Any attempt to run
with a real adapter fails at startup instead of silently choosing a provider (RC12).
"""

import os
from dataclasses import dataclass

ALLOWED_MODES = frozenset({"mock"})
GRAPH_API_VERSION = "v26.0"  # pinned in Technical spec v1.3; not configurable


class GateBlockedError(RuntimeError):
    """Raised when config asks for something an open gate (G01-G06) has not cleared."""


@dataclass(frozen=True)
class Settings:
    mode: str
    database_url: str | None
    model_adapter: str
    feed_adapter: str


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
    return Settings(
        mode=mode,
        database_url=env.get("DESK_DATABASE_URL"),
        model_adapter=model_adapter,
        feed_adapter=feed_adapter,
    )
