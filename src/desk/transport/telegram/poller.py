"""Long polling (getUpdates) -> the same onboarding/chat handling as WhatsApp.

Only private chats with real people are handled; groups, channels and other bots are
skipped. The getUpdates offset is stored in Postgres so a restart neither loses nor
re-handles messages. Inbound messages are deduplicated by (bot, chat, message_id), which
Telegram never reuses, NOT by update_id: update_ids restart at a random value after a
week without updates (MED-6). After STALE_CURSOR idle the stored offset is not trusted
and polling restarts from offset 0 (all still-unconfirmed updates; the dedupe key makes
that safe). Telegram keeps unconfirmed updates for 24 hours only: messages sent while
the desk is off for more than a day are lost (owner-visible note in GATES.md, T1).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from desk.config import TelegramSettings
from desk.db.models import TransportCursor
from desk.transport.telegram.client import TelegramClient
from desk.transport.whatsapp.payload import InboundMessage
from desk.transport.whatsapp.webhook import _process

# Telegram re-randomises update_id after >= 7 days without updates. Polling with the old
# (higher) offset would then confirm, i.e. silently drop, the new lower ids, so the switch
# to offset 0 must happen BEFORE day 7.
STALE_CURSOR = timedelta(days=6)


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
        message_id=f"tg:{bot_id}:{chat['id']}:{msg.get('message_id')}",
        waba_id="",
        phone_number_id=bot_id,
        sender=str(chat["id"])[:20],
        provider_time=datetime.fromtimestamp(int(msg.get("date", 0)), tz=UTC),
        type="text" if isinstance(text, str) else "other",
        text=text[:4096] if isinstance(text, str) else None,
        channel="telegram",
    )


def _offset(factory: sessionmaker[Session], bot_id: str, now: datetime) -> int:
    """Next getUpdates offset. 0 = "everything not yet confirmed" (first run, or the
    stored cursor is older than STALE_CURSOR and may be above re-randomised ids)."""
    with factory() as s:
        row = s.get(TransportCursor, ("telegram", bot_id))
        if row is None or now - row.updated_at > STALE_CURSOR:
            return 0
        return row.last_update_id + 1


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
    offset = _offset(factory, tg.bot_id, now)
    updates = client.get_updates(offset=offset, timeout_s=timeout_s)
    last = offset - 1 if offset else 0
    handled = skipped = 0
    state = SimpleNamespace(settings=SimpleNamespace(whatsapp=tg), session_factory=factory)
    for update in sorted(updates, key=lambda u: u["update_id"]):
        msg = to_inbound(update, tg.bot_id)
        if msg is None:
            skipped += 1
        else:
            _process(state, [msg], now)
            handled += 1
        last = int(update["update_id"])  # sorted batch: the newest handled so far
        _save_cursor(factory, tg.bot_id, last, now)  # after handling: at-least-once
    return PollResult(len(updates), handled, skipped, last)
