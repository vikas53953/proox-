"""Builders for signed Cloud API webhook payloads used by the constitution tests."""

import itertools
import json
import time
from datetime import UTC, datetime

from desk.transport.whatsapp.payload import InboundMessage
from desk.transport.whatsapp.signature import expected_signature

_ids = itertools.count(1)


def message(
    sender: str,
    text: str | None,
    *,
    msg_id: str | None = None,
    msg_type="text",
    profile_name: str = "Someone",
) -> dict:
    m = {
        "from": sender,
        "id": msg_id or f"wamid.TEST{next(_ids)}",
        "timestamp": str(int(time.time())),
        "type": msg_type,
    }
    if msg_type == "text":
        m["text"] = {"body": text}
    return {"message": m, "profile_name": profile_name}


def payload(
    wa, *msgs: dict, phone_number_id: str | None = None, waba_id: str | None = None
) -> bytes:
    contacts = [
        {"profile": {"name": m["profile_name"]}, "wa_id": m["message"]["from"]} for m in msgs
    ]
    doc = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id or wa.waba_id,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "910000000000",
                                "phone_number_id": phone_number_id or wa.phone_number_id,
                            },
                            "contacts": contacts,
                            "messages": [m["message"] for m in msgs],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(doc).encode()


def post(client, wa, body: bytes, *, secret: str | None = None, signature: str | None = None):
    sig = signature if signature is not None else expected_signature(secret or wa.app_secret, body)
    headers = {"content-type": "application/json"}
    if sig:
        headers["x-hub-signature-256"] = sig
    return client.post("/webhooks/whatsapp", content=body, headers=headers)


def inbound(wa, sender: str, text: str, msg_id: str) -> InboundMessage:
    return InboundMessage(
        message_id=msg_id,
        waba_id=wa.waba_id,
        phone_number_id=wa.phone_number_id,
        sender=sender,
        provider_time=datetime.now(UTC),
        type="text",
        text=text,
    )
