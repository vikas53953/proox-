"""WhatsApp Cloud API behind the channel-neutral Transport interface."""

from desk.transport.base import WHATSAPP, Outcome, SendResult
from desk.transport.whatsapp.client import (
    GraphClient,
    media_payload,
    template_payload,
    text_payload,
)


class WhatsAppTransport:
    caps = WHATSAPP

    def __init__(self, client: GraphClient) -> None:
        self.client = client

    def send_text(self, to: str, body: str, ref: str) -> Outcome:
        return self.client.send(text_payload(to, body, ref))

    def send_media(
        self, to: str, kind: str, content: bytes, mime: str, filename: str, caption: str, ref: str
    ) -> Outcome:
        up = self.client.upload(content, mime, filename)  # safe to repeat: sends nothing
        if up.result is not SendResult.ACCEPTED:
            return up
        return self.client.send(
            media_payload(to, kind, up.provider_message_id, caption, filename, ref)
        )

    def send_template(
        self, to: str, name: str, language: str, params: list[str], ref: str
    ) -> Outcome:
        return self.client.send(template_payload(to, name, language, params, ref))
