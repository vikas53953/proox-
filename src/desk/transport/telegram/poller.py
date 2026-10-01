"""Long polling (getUpdates) -> the same onboarding/chat handling as WhatsApp.

Only private chats with real people are handled; groups, channels and other bots are
skipped. The getUpdates offset is stored in Postgres so a restart neither loses nor
re-handles messages (inbound message ids are also deduplicated, as for WhatsApp).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from desk.config import TelegramSettings
from desk.db.models import TransportCursor
from desk.transport.telegram.client import TelegramClient
from desk.transport.whatsapp.payload import InboundMessage
from desk.transport.whatsapp.webhook import _process


@dataclass(frozen=True)
class PollResult:
    received: int
    handled: int
    skipped: int
    offset: int


def to_inbound(update: dict, bot_id: str) -> InboundMessage | None:
    msg = update.get("message") or {}
    chat, sender = msg.get("chat") or {}, msg.get("from") or {}
    if chat.get("type") != "private" or sender.get("is_bot", True):
        return None
    text = msg.get("text")
    return InboundMessage(
        message_id=f"tg:{bot_id}:{update['update_id']}",
        waba_id="",
        phone_number_id=bot_id,
        sender=str(chat["id"])[:20],
        provider_time=datetime.fromtimestamp(int(msg.get("date", 0)), tz=UTC),
        type="text" if isinstance(text, str) else "other",
        text=text[:4096] if isinstance(text, str) else None,
        channel="telegram",
    )


def _cursor(factory: sessionmaker[Session], bot_id: str) -> int:
    with factory() as s:
        row = s.get(TransportCursor, ("telegram", bot_id))
        return row.last_update_id if row else 0


def _save_cursor(factory: sessionmaker[Session], bot_id: str, last: int, now: datetime) -> None:
    with factory() as s:
        s.execute(
            pg_insert(TransportCursor)
            .values(
                channel="telegram",
                business_id=bot_id,
                last_update_id=last,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["channel", "business_id"],
                set_={"last_update_id": last, "updated_at": now},
            )
        )
        s.commit()


def poll_once(
    factory: sessionmaker[Session],
    client: TelegramClient,
    tg: TelegramSettings,
    now: datetime,
    timeout_s: int = 0,
) -> PollResult:
    last = _cursor(factory, tg.bot_id)
    updates = client.get_updates(offset=last + 1, timeout_s=timeout_s)
    handled = skipped = 0
    state = SimpleNamespace(settings=SimpleNamespace(whatsapp=tg), session_factory=factory)
    for update in sorted(updates, key=lambda u: u["update_id"]):
        msg = to_inbound(update, tg.bot_id)
        if msg is None:
            skipped += 1
        else:
            _process(state, [msg], now)
            handled += 1
        last = max(last, int(update["update_id"]))
        _save_cursor(factory, tg.bot_id, last, now)  # after handling: at-least-once
    return PollResult(len(updates), handled, skipped, last)
