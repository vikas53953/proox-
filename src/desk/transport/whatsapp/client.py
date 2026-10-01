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
from dataclasses import dataclass
from enum import StrEnum

import httpx

from desk.config import GRAPH_API_VERSION

GRAPH_BASE = "https://graph.facebook.com"
RETRY_CODES = frozenset({"4", "80007", "130429", "131048", "131056"})  # rate / throughput
WINDOW_CODES = frozenset({"131047"})  # re-engagement: outside 24h window
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class SendResult(StrEnum):
    ACCEPTED = "ACCEPTED"
    RETRY = "RETRY"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Outcome:
    result: SendResult
    provider_message_id: str | None = None
    detail: str = ""


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


class GraphClient:
    """Thin client. `http` is injected: a MockTransport while G02 is BLOCKED."""

    def __init__(self, http: httpx.Client, phone_number_id: str, access_token: str) -> None:
        self._http = http
        self._url = f"{GRAPH_BASE}/{GRAPH_API_VERSION}/{phone_number_id}/messages"
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


def make_client(settings) -> "GraphClient":
    """Only mock mode exists while G02 is BLOCKED: no request can reach graph.facebook.com."""
    from desk.config import GateBlockedError

    if settings.mode != "mock":
        raise GateBlockedError("real WhatsApp sending BLOCKED by G02")
    return FakeGraph().client(settings.whatsapp.phone_number_id or "100200300")


class FakeGraph:
    """In-memory stand-in for graph.facebook.com used in mock mode and tests.
    Nothing leaves the machine. Behaviour per call can be scripted."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.script: list = []  # items: "ok" | int status | dict body | Exception
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
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
                    "messages": [{"id": f"wamid.MOCK{self._n}"}],
                },
            )
        if isinstance(step, int):
            return httpx.Response(step, text="error")
        return httpx.Response(400, json=step)

    def client(self, phone_number_id: str = "100200300") -> GraphClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return GraphClient(http, phone_number_id, "MOCK-TOKEN")
