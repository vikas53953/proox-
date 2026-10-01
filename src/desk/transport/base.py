"""Channel-neutral transport interface (WhatsApp = product channel; Telegram = owner-
approved TEST transport). Policy reads a channel's capabilities instead of assuming
WhatsApp rules, and delivery states never claim more than the channel reports.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol


class SendResult(StrEnum):
    ACCEPTED = "ACCEPTED"  # WhatsApp: Graph took it; receipts may follow
    SENT = "SENT"  # Telegram: accepted by Telegram's servers; nothing further is reported
    RETRY = "RETRY"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # the person blocked the bot: treat as opt-out
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Outcome:
    result: SendResult
    provider_message_id: str | None = None
    detail: str = ""
    retry_after_s: int | None = None


@dataclass(frozen=True)
class ChannelCaps:
    name: str
    service_window: timedelta | None  # None = no window: may message an opted-in user any time
    needs_templates: bool  # outside the window, only approved templates
    delivery_receipts: bool
    read_receipts: bool
    callback_reconcile: bool  # can an UNKNOWN send be reconciled from a receipt?
    max_text: int
    max_caption: int
    delivery_note: str  # shown wherever a delivery state is displayed


WHATSAPP = ChannelCaps(
    "whatsapp",
    timedelta(hours=24),
    True,
    True,
    True,
    True,
    4096,
    1024,
    "delivery and read receipts reported by WhatsApp",
)
TELEGRAM = ChannelCaps(
    "telegram",
    None,
    False,
    False,
    False,
    False,
    4096,
    1024,
    "Telegram reports no delivery or read receipts; SENT is final",
)
CAPS = {c.name: c for c in (WHATSAPP, TELEGRAM)}


class Transport(Protocol):
    caps: ChannelCaps

    def send_text(self, to: str, body: str, ref: str) -> Outcome: ...

    def send_media(
        self, to: str, kind: str, content: bytes, mime: str, filename: str, caption: str, ref: str
    ) -> Outcome: ...

    def send_template(
        self, to: str, name: str, language: str, params: list[str], ref: str
    ) -> Outcome: ...
