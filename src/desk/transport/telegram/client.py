"""Minimal Telegram Bot API client over the pinned HTTPX (no Telegram SDK — BOM).

Only long-stable methods are used: getMe, getUpdates, sendMessage, sendDocument,
sendPhoto. Plain text only (no parse_mode), so report text can't be turned into markup.
"""

import json
import re
import uuid

import httpx

from desk.transport.base import TELEGRAM, Outcome, SendResult

API_BASE = "https://api.telegram.org"
# Placeholder for the in-memory fake only; real tokens come from the environment.
MOCK_TOKEN = "0:MOCK"  # noqa: S105 - not token-shaped on purpose (E01 scan stays strict)
TIMEOUT = httpx.Timeout(35.0, connect=5.0)  # long-poll waits up to 25 s


class TelegramClient:
    def __init__(self, http: httpx.Client, token: str) -> None:
        self._http = http
        self._base = f"{API_BASE}/bot{token}"  # contains the secret: never log or echo

    def _call(
        self, method: str, data: dict | None = None, files: dict | None = None
    ) -> httpx.Response:
        return self._http.post(f"{self._base}/{method}", data=data, files=files, timeout=TIMEOUT)

    def get_me(self) -> dict:
        r = self._call("getMe")
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError("getMe failed: check the bot token")
        return body["result"]

    def get_updates(self, offset: int, timeout_s: int = 25) -> list[dict]:
        r = self._call(
            "getUpdates",
            data={
                "offset": offset,
                "timeout": timeout_s,
                "allowed_updates": json.dumps(["message"]),
            },
        )
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"getUpdates failed: {body.get('description', '')[:100]}")
        return body["result"]

    def send(self, method: str, data: dict, files: dict | None = None) -> Outcome:
        try:
            r = self._call(method, data=data, files=files)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return Outcome(SendResult.RETRY, detail="connection failed before sending")
        except httpx.TransportError as exc:
            return Outcome(
                SendResult.UNKNOWN, detail=f"no answer after sending: {type(exc).__name__}"
            )
        if r.status_code >= 500:
            return Outcome(SendResult.UNKNOWN, detail=f"HTTP {r.status_code}")
        try:
            body = r.json()
        except json.JSONDecodeError:
            return Outcome(SendResult.UNKNOWN, detail="unreadable response")
        if body.get("ok"):
            msg = body.get("result") or {}
            mid = f"tg:{msg.get('chat', {}).get('id')}:{msg.get('message_id')}"
            return Outcome(SendResult.SENT, provider_message_id=mid)
        code = int(body.get("error_code", r.status_code))
        desc = str(body.get("description", ""))[:200]
        if code == 429:
            retry = (body.get("parameters") or {}).get("retry_after")
            return Outcome(
                SendResult.RETRY, detail=f"429: {desc}", retry_after_s=int(retry) if retry else None
            )
        if code == 403:
            return Outcome(SendResult.BLOCKED, detail=f"403: {desc}")
        return Outcome(SendResult.FAILED, detail=f"{code}: {desc}")


class TelegramTransport:
    caps = TELEGRAM

    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    def send_text(self, to: str, body: str, ref: str) -> Outcome:
        return self.client.send(
            "sendMessage",
            {"chat_id": to, "text": body[: TELEGRAM.max_text], "disable_web_page_preview": "true"},
        )

    def send_media(
        self, to: str, kind: str, content: bytes, mime: str, filename: str, caption: str, ref: str
    ) -> Outcome:
        method, field = ("sendPhoto", "photo") if kind == "image" else ("sendDocument", "document")
        return self.client.send(
            method,
            {"chat_id": to, "caption": caption[: TELEGRAM.max_caption]},
            files={field: (filename, content, mime)},
        )

    def send_template(
        self, to: str, name: str, language: str, params: list[str], ref: str
    ) -> Outcome:
        return Outcome(SendResult.FAILED, detail="Telegram has no message templates")


class FakeTelegram:
    """In-memory api.telegram.org for tests and the container (Telegram is blocked here)."""

    def __init__(self, bot_id: int = 777000111, username: str = "desk_mock_bot") -> None:
        self.bot_id, self.username = bot_id, username
        self.calls: list[dict] = []
        self.updates: list[dict] = []
        self.script: list = []  # per send: "ok" | dict error body | int status | Exception
        self._next_update = 1000
        self.confirmed_offset = 0

    def user_says(
        self, user_id: int, text: str, chat_type: str = "private", is_bot: bool = False
    ) -> int:
        self._next_update += 1
        self.updates.append(
            {
                "update_id": self._next_update,
                "message": {
                    "message_id": self._next_update,
                    "date": 1790000000,
                    "text": text,
                    "chat": {"id": user_id, "type": chat_type},
                    "from": {"id": user_id, "is_bot": is_bot, "first_name": "MOCK"},
                },
            }
        )
        return self._next_update

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"id": self.bot_id, "is_bot": True, "username": self.username},
                },
            )
        if method == "getUpdates":
            form = dict(httpx.QueryParams(request.content.decode()))
            offset = int(form.get("offset", 0))
            self.confirmed_offset = max(self.confirmed_offset, offset)
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
            return httpx.Response(200, json={"ok": True, "result": list(self.updates)})
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/"):
            fields = {"multipart": True, "bytes": len(request.content)}
            body = request.content.decode("latin-1")
            m = re.search(r'name="chat_id"\r\n\r\n([^\r]*)', body)
            fields["chat_id"] = m.group(1) if m else None
            c = re.search(r'name="caption"\r\n\r\n(.*?)\r\n--', body, re.S)
            fields["caption"] = c.group(1) if c else ""
        else:
            fields = dict(httpx.QueryParams(request.content.decode()))
        self.calls.append({"method": method, **fields})
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step, text="error")
        if isinstance(step, dict):
            return httpx.Response(step.get("error_code", 400), json={"ok": False, **step})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": uuid.uuid4().int % 10**9,
                    "chat": {"id": int(fields.get("chat_id") or 0), "type": "private"},
                },
            },
        )

    def client(self, token: str = MOCK_TOKEN) -> TelegramClient:
        return TelegramClient(httpx.Client(transport=httpx.MockTransport(self.handler)), token)
