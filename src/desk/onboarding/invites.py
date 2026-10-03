"""Invite codes: opaque, high-entropy, shown once to the operator, stored only as a hash."""

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from desk.db.models import Invite

CODE_PREFIX = "PX-"
CODE_RE = re.compile(r"\bPX-[A-Z2-7]{26}\b", re.IGNORECASE)


def new_code() -> str:
    return CODE_PREFIX + base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")


def hash_code(code: str) -> str:
    return hashlib.sha256(code.upper().encode()).hexdigest()


def find_code(text: str | None) -> str | None:
    m = CODE_RE.search(text or "")
    return m.group(0).upper() if m else None


def redact(text: str | None) -> str:
    return CODE_RE.sub("[invite code]", text or "")


TELEGRAM_MAX_TTL = timedelta(hours=24)  # owner rule: deep links are forwardable


def create_invite(
    session: Session,
    *,
    business_phone_id: str,
    now: datetime,
    ttl: timedelta,
    bound_sender: str | None = None,
    with_code: bool = True,
    channel: str = "whatsapp",
) -> tuple[Invite, str | None]:
    """Create an invite. Returns the plain code ONCE; only its hash is stored.

    WhatsApp: with_code=False + bound_sender = "preapproved sender" (Hi is enough).
    Telegram: always a single-use code, at most 24 hours. Optional pre-binding (GATES.md
    T1): bound_sender = the expected chat id (an integer); only that chat can consume the
    code, any other chat gets the neutral reply and the invite stays open.
    """
    if not with_code and not bound_sender:
        raise ValueError("an invite needs a code, a bound sender, or both")
    if channel == "telegram":
        if not with_code:
            raise ValueError("Telegram invites always need a code (deep link)")
        ttl = min(ttl, TELEGRAM_MAX_TTL)
        if bound_sender is not None:
            bound_sender = telegram_chat_id(bound_sender)
    code = new_code() if with_code else None
    invite = Invite(
        token_hash=hash_code(code) if code else None,
        bound_sender=bound_sender,
        business_phone_id=business_phone_id,
        created_at=now,
        expires_at=now + ttl,
        state="open",
        channel=channel,
    )
    session.add(invite)
    session.flush()
    return invite, code


_TG_CHAT_ID = re.compile(r"-?[0-9]{1,19}")


def telegram_chat_id(raw: str | int) -> str:
    """Normalise a Telegram chat id to the form the poller stores (str(int)); refuse
    anything that is not an integer, so a typo can never create an unreachable binding."""
    text = str(raw).strip()
    if not _TG_CHAT_ID.fullmatch(text):
        raise ValueError("a Telegram chat id is an integer")
    return str(int(text))


def telegram_deep_link(bot_username: str, code: str) -> str:
    """t.me link whose start parameter is the invite code (<=64 chars, A-Z0-9_-)."""
    return f"https://t.me/{bot_username}?start={code}"
