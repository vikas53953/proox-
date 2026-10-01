"""Parsing of the Cloud API `messages` webhook (S80). Only fields we use are read.

Identity comes from `messages[].from` (the transport). The contact profile name and any
number typed inside the text are never used as identity.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

WA_ID = re.compile(r"^\d{6,20}$")
MAX_BODY_BYTES = 256 * 1024


class PayloadError(ValueError):
    pass


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    waba_id: str
    phone_number_id: str
    sender: str
    provider_time: datetime
    type: str
    text: str | None


@dataclass(frozen=True)
class StatusUpdate:
    provider_message_id: str
    status: str  # sent / delivered / read / failed
    provider_time: datetime
    recipient: str
    callback: str | None  # our biz_opaque_callback_data (the outbox id)
    error_code: str | None
    error_title: str | None


def parse_statuses(doc: Any) -> list[StatusUpdate]:
    out: list[StatusUpdate] = []
    for entry in doc.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            for st in (change.get("value") or {}).get("statuses") or []:
                errors = st.get("errors") or [{}]
                out.append(
                    StatusUpdate(
                        provider_message_id=str(st["id"])[:128],
                        status=str(st["status"]).lower()[:16],
                        provider_time=datetime.fromtimestamp(int(st["timestamp"]), tz=UTC),
                        recipient=str(st.get("recipient_id", ""))[:20],
                        callback=st.get("biz_opaque_callback_data"),
                        error_code=str(errors[0]["code"]) if "code" in errors[0] else None,
                        error_title=errors[0].get("title"),
                    )
                )
    return out


def business_ids(doc: Any) -> set[tuple[str, str]]:
    """(waba_id, phone_number_id) pairs present in the webhook, for the business check."""
    pairs = set()
    for entry in doc.get("entry") or []:
        for change in entry.get("changes") or []:
            meta = (change.get("value") or {}).get("metadata") or {}
            pairs.add((str(entry.get("id", "")), str(meta.get("phone_number_id", ""))))
    return pairs


def parse_messages(doc: Any) -> list[InboundMessage]:
    if not isinstance(doc, dict) or doc.get("object") != "whatsapp_business_account":
        raise PayloadError("not a whatsapp_business_account webhook")
    out: list[InboundMessage] = []
    for entry in doc.get("entry") or []:
        waba_id = str(entry.get("id", ""))
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id", ""))
            for m in value.get("messages") or []:
                sender = str(m.get("from", ""))
                if not WA_ID.match(sender):
                    raise PayloadError("sender is not a WhatsApp id")
                msg_type = str(m.get("type", "unknown"))[:32]
                text = (m.get("text") or {}).get("body") if msg_type == "text" else None
                out.append(
                    InboundMessage(
                        message_id=str(m["id"])[:128],
                        waba_id=waba_id,
                        phone_number_id=phone_number_id,
                        sender=sender,
                        provider_time=datetime.fromtimestamp(int(m["timestamp"]), tz=UTC),
                        type=msg_type,
                        text=text[:4096] if isinstance(text, str) else None,
                    )
                )
    return out
