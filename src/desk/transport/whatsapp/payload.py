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
