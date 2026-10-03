"""Cloud API v26.0 send client: POST /{phone_number_id}/messages via HTTPX (S78).

Every send ends in exactly one Outcome:
- ACCEPTED: Graph returned a message id (accepted is NOT delivered).
- RETRY: the request certainly never reached Meta (connection refused / DNS) or Meta
  said "slow down" (rate / throughput codes). Safe to try again later.
- WINDOW_CLOSED: Meta says the 24-hour window is closed (re-engagement rule).
- FAILED: Meta rejected it permanently (bad number, bad template, policy...).
- UNKNOWN: we cannot know whether it was sent (timeout after sending, 5xx, broken
  response). Never resent blindly; reconciled only from a status receipt.

Error-code groups below follow Meta's Cloud API error-code reference as understood at
build time; they must be re-checked against v26.0 when G02 is closed.
"""

import json
import uuid

import httpx

from desk.config import GRAPH_API_VERSION
from desk.transport.base import Outcome, SendResult

GRAPH_BASE = "https://graph.facebook.com"
RETRY_CODES = frozenset({"4", "80007", "130429", "131048", "131056"})  # rate / throughput
WINDOW_CODES = frozenset({"131047"})  # re-engagement: outside 24h window
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def text_payload(to: str, body: str, callback: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
        "biz_opaque_callback_data": callback,
    }


def template_payload(to: str, name: str, language: str, params: list[str], callback: str) -> dict:
    components = (
        [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
        if params
        else []
    )
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": name, "language": {"code": language}, "components": components},
        "biz_opaque_callback_data": callback,
    }


CAPTION_MAX = 1024  # WhatsApp media caption limit (verify at G02)


def media_payload(
    to: str, kind: str, media_id: str, caption: str, filename: str, callback: str
) -> dict:
    body = {"id": media_id, "caption": caption[:CAPTION_MAX]}
    if kind == "document":
        body["filename"] = filename
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": kind,
        kind: body,
        "biz_opaque_callback_data": callback,
    }


class GraphClient:
    """Thin client. `http` is injected: a MockTransport while G02 is BLOCKED."""

    def __init__(self, http: httpx.Client, phone_number_id: str, access_token: str) -> None:
        self._http = http
        self.phone_number_id = phone_number_id
        self._url = f"{GRAPH_BASE}/{GRAPH_API_VERSION}/{phone_number_id}/messages"
        self._media_url = f"{GRAPH_BASE}/{GRAPH_API_VERSION}/{phone_number_id}/media"
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def send(self, payload: dict) -> Outcome:
        try:
            r = self._http.post(self._url, json=payload, headers=self._headers, timeout=TIMEOUT)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return Outcome(SendResult.RETRY, detail="connection failed before sending")
        except httpx.TransportError as exc:  # read timeout, reset after sending, ...
            return Outcome(
                SendResult.UNKNOWN, detail=f"no answer after sending: {type(exc).__name__}"
            )
        if r.status_code >= 500:
            return Outcome(SendResult.UNKNOWN, detail=f"HTTP {r.status_code}")
        try:
            body = r.json()
        except json.JSONDecodeError:
            return Outcome(SendResult.UNKNOWN, detail="unreadable response")
        if r.status_code == 200:
            try:
                return Outcome(SendResult.ACCEPTED, provider_message_id=body["messages"][0]["id"])
            except (KeyError, IndexError, TypeError):
                return Outcome(SendResult.UNKNOWN, detail="200 without message id")
        err = body.get("error") or {}
        code = str(err.get("code", r.status_code))
        detail = f"code {code}: {str(err.get('message', ''))[:200]}"
        if code in WINDOW_CODES:
            return Outcome(SendResult.WINDOW_CLOSED, detail=detail)
        if code in RETRY_CODES or r.status_code == 429:
            return Outcome(SendResult.RETRY, detail=detail)
        return Outcome(SendResult.FAILED, detail=detail)

    def upload(self, content: bytes, mime: str, filename: str) -> Outcome:
        """Upload media first; returns its media id. Uploading twice only wastes an id —
        no message is sent by an upload — so any unclear upload result is a safe RETRY."""
        try:
            r = self._http.post(
                self._media_url,
                headers=self._headers,
                timeout=TIMEOUT,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, content, mime)},
            )
        except httpx.TransportError as exc:
            return Outcome(SendResult.RETRY, detail=f"upload: {type(exc).__name__}")
        if r.status_code >= 500 or r.status_code == 429:
            return Outcome(SendResult.RETRY, detail=f"upload HTTP {r.status_code}")
        try:
            body = r.json()
        except json.JSONDecodeError:
            return Outcome(SendResult.RETRY, detail="upload: unreadable response")
        if r.status_code == 200 and body.get("id"):
            return Outcome(SendResult.ACCEPTED, provider_message_id=str(body["id"]))
        return Outcome(SendResult.FAILED, detail=f"upload rejected: {str(body)[:200]}")


MOCK_PHONE_NUMBER_ID = "100200300"  # the fake's own number; never a real one


def make_client(settings) -> "GraphClient":
    """Only mock mode exists while G02 is BLOCKED: no request can reach graph.facebook.com.
    B08: the fake always uses its OWN mock phone id (like FakeTelegram's own bot id), so
    rows of a real WHATSAPP_PHONE_NUMBER_ID are never claimed and marked ACCEPTED by it."""
    from desk.config import GateBlockedError

    if settings.mode != "mock":
        raise GateBlockedError("real WhatsApp sending BLOCKED by G02")
    return FakeGraph().client(MOCK_PHONE_NUMBER_ID)


def make_send_client(settings) -> "GraphClient":
    """The client `serve` sends with. B08 (HIGH-1 rule for WhatsApp): a real access token
    with the in-memory fake could make real rows look ACCEPTED while nothing is sent, so
    the fake refuses to start while WHATSAPP_ACCESS_TOKEN is set."""
    from desk.config import GateBlockedError

    if settings.whatsapp.access_token:
        raise GateBlockedError(
            "WHATSAPP_ACCESS_TOKEN is set but real WhatsApp sending is BLOCKED (G02): "
            "refusing the in-memory fake. Unset the token for mock runs."
        )
    return make_client(settings)


class FakeGraph:
    """In-memory stand-in for graph.facebook.com used in mock mode and tests.
    Nothing leaves the machine. Behaviour per call can be scripted."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.uploads: list[dict] = []
        self.script: list = []  # items: "ok" | int status | dict body | Exception
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media"):
            self.uploads.append(
                {"bytes": len(request.content), "type": request.headers.get("content-type", "")}
            )
            return httpx.Response(200, json={"id": f"media.MOCK{len(self.uploads)}"})
        payload = json.loads(request.content)
        self.requests.append(payload)
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        if step == "ok":
            self._n += 1
            return httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "messages": [{"id": f"wamid.MOCK{uuid.uuid4().hex[:16]}"}],
                },
            )
        if isinstance(step, int):
            return httpx.Response(step, text="error")
        return httpx.Response(400, json=step)

    def client(self, phone_number_id: str = MOCK_PHONE_NUMBER_ID) -> GraphClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return GraphClient(http, phone_number_id, "MOCK-TOKEN")
