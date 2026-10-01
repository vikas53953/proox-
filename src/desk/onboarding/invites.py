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


def create_invite(
    session: Session,
    *,
    business_phone_id: str,
    now: datetime,
    ttl: timedelta,
    bound_sender: str | None = None,
    with_code: bool = True,
) -> tuple[Invite, str | None]:
    """Create an invite. Returns the plain code ONCE; only its hash is stored.

    with_code=False + bound_sender = "preapproved sender": that number can just say Hi.
    """
    if not with_code and not bound_sender:
        raise ValueError("an invite needs a code, a bound sender, or both")
    code = new_code() if with_code else None
    invite = Invite(
        token_hash=hash_code(code) if code else None,
        bound_sender=bound_sender,
        business_phone_id=business_phone_id,
        created_at=now,
        expires_at=now + ttl,
        state="open",
    )
    session.add(invite)
    session.flush()
    return invite, code
