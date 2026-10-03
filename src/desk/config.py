"""Settings read from environment variables only. No secret ever lives in source.

While gates G01-G06 are BLOCKED, the only allowed mode is "mock". Any attempt to run
with a real adapter fails at startup instead of silently choosing a provider (RC12).
Secret fields are excluded from repr so they cannot leak into logs (RC08).
"""

import os
import re
from dataclasses import dataclass, field

from desk.pii import SenderKeyError, parse_key

ALLOWED_MODES = frozenset({"mock"})
# Telegram Bot API version this code was checked against (RC12 pin, see BOM.md).
# None = not yet pinned: live Telegram refuses to start until the owner records it.
TELEGRAM_BOT_API_VERSION: str | None = None
GRAPH_API_VERSION = "v26.0"  # pinned in Technical spec v1.3; not configurable


class GateBlockedError(RuntimeError):
    """Raised when config asks for something an open gate (G01-G06) has not cleared."""


class SettingsError(ValueError):
    """A setting has a value we cannot read; startup is refused with a clear message."""


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

    def accepts(self, msg) -> bool:
        """Only our own WhatsApp business number / account."""
        return (
            getattr(msg, "channel", "whatsapp") == "whatsapp"
            and msg.phone_number_id == self.phone_number_id
            and msg.waba_id == self.waba_id
        )


@dataclass(frozen=True)
class TelegramSettings:
    """Owner-approved TEST transport. Token: env var on the owner's PC until the G05
    secret store exists; never in code, chat, logs or commits."""

    bot_token: str = field(default="", repr=False)
    bot_username: str = ""
    live: bool = False  # DESK_TELEGRAM_LIVE=1: real api.telegram.org (owner PC only)
    # B07: TELEGRAM_ALLOWED_CHAT_IDS. None = unset = no restriction (today's behaviour).
    # Set: only these chat ids may bind an invite, be handled or receive tenant messages.
    allowed_chat_ids: frozenset[str] | None = None
    # TELEGRAM_PRIVACY_NOTICE=1: send the (DRAFT) privacy disclosure once, right after a
    # Telegram welcome (GATES.md T1). Off by default.
    privacy_notice: bool = False

    @property
    def bot_id(self) -> str:
        head = self.bot_token.split(":", 1)[0]
        return head if head.isdigit() else "0"

    def accepts(self, msg) -> bool:
        return getattr(msg, "channel", "") == "telegram" and msg.phone_number_id == self.bot_id

    def allows(self, chat_id: str) -> bool:
        """B07 allowlist check; always True while TELEGRAM_ALLOWED_CHAT_IDS is unset."""
        return self.allowed_chat_ids is None or str(chat_id) in self.allowed_chat_ids


@dataclass(frozen=True)
class SenderCryptoSettings:
    """B02: encrypt transport ids at rest. OFF by default; the key is ignored when OFF.
    Key: env var until the G05 key store exists; never in repr, logs or commits."""

    enabled: bool = False
    key: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Settings:
    mode: str
    database_url: str | None = field(repr=False)  # URL may embed a password
    model_adapter: str
    feed_adapter: str
    whatsapp: WhatsAppSettings = field(default_factory=WhatsAppSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    senders: SenderCryptoSettings = field(default_factory=SenderCryptoSettings)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    mode = env.get("DESK_MODE", "mock")
    if mode not in ALLOWED_MODES:
        raise GateBlockedError(f"DESK_MODE={mode!r} not allowed: gates G01-G06 are BLOCKED")
    model_adapter = env.get("DESK_MODEL_ADAPTER", "mock")
    if model_adapter != "mock":
        raise GateBlockedError("model adapter BLOCKED by G01")
    feed_adapter = env.get("DESK_FEED_ADAPTER", "fixture")
    if feed_adapter == "nse_public":
        raise GateBlockedError(
            "feed adapter 'nse_public' is built but DISABLED: G03 is BLOCKED until the owner "
            "confirms NSE usage / redistribution / AI-use terms (GATES.md, G03 staged direction)"
        )
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
        telegram=TelegramSettings(
            bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            bot_username=env.get("TELEGRAM_BOT_USERNAME", ""),
            live=env.get("DESK_TELEGRAM_LIVE", "") == "1",
            allowed_chat_ids=parse_chat_ids(env.get("TELEGRAM_ALLOWED_CHAT_IDS", "")),
            privacy_notice=_flag(env, "TELEGRAM_PRIVACY_NOTICE"),
        ),
        senders=_sender_crypto(env),
    )


def _flag(env: dict[str, str], name: str) -> bool:
    """Empty or "0" = off (default), "1" = on; anything else refuses to start."""
    raw = env.get(name, "").strip()
    if raw not in ("", "0", "1"):
        raise SettingsError(f"{name} must be 0 or 1")
    return raw == "1"


_CHAT_ID = re.compile(r"-?[0-9]{1,19}")


def parse_chat_ids(raw: str) -> frozenset[str] | None:
    """B07: "111, 222" -> {"111", "222"}; empty/unset -> None (no restriction).
    Anything else (non-integers, or only commas) refuses to start: a typo must never
    silently turn the allowlist off."""
    if not raw.strip():
        return None
    ids = [part.strip() for part in raw.split(",")]
    bad = [i for i in ids if i and not _CHAT_ID.fullmatch(i)]
    if bad or not any(ids):
        raise SettingsError(
            "TELEGRAM_ALLOWED_CHAT_IDS must be comma-separated integer chat ids "
            "(e.g. 123456789,987654321) or empty"
        )
    return frozenset(str(int(i)) for i in ids if i)


def _sender_crypto(env: dict[str, str]) -> SenderCryptoSettings:
    """DESK_ENCRYPT_SENDERS=1 needs a valid DESK_SENDER_KEY, else startup is refused
    (SenderKeyError, message never contains the key). OFF ignores the key."""
    flag = env.get("DESK_ENCRYPT_SENDERS", "0").strip()
    if flag in ("", "0"):
        return SenderCryptoSettings()
    if flag != "1":
        raise SenderKeyError("DESK_ENCRYPT_SENDERS must be 0 or 1")
    return SenderCryptoSettings(enabled=True, key=parse_key(env.get("DESK_SENDER_KEY", "")))
