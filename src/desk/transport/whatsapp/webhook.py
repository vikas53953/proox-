"""GET /webhooks/whatsapp: subscription verify. POST: signed message intake (S80, S81).

POST order: size bound -> HMAC signature on raw body -> JSON parse -> per message, one
transaction (dedupe + onboarding) -> 200. A bad signature is rejected before any parsing
or storage. Responses never echo payload content.
"""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from desk.onboarding.service import handle_message
from desk.outbox.receipts import apply_status
from desk.transport.whatsapp.payload import (
    MAX_BODY_BYTES,
    PayloadError,
    business_ids,
    parse_messages,
    parse_statuses,
)
from desk.transport.whatsapp.signature import signature_valid, verify_token_valid

router = APIRouter()
PATH = "/webhooks/whatsapp"


@router.get(PATH)
def verify(request: Request) -> Response:
    wa = request.app.state.settings.whatsapp
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and verify_token_valid(
        wa.verify_token, q.get("hub.verify_token")
    ):
        return PlainTextResponse(q.get("hub.challenge", ""))
    return Response(status_code=403)


def _process(app_state, messages, now: datetime, statuses=()) -> None:
    wa = app_state.settings.whatsapp
    for st in statuses:
        with app_state.session_factory() as session:
            apply_status(session, st, now)
            session.commit()
    for msg in messages:
        for attempt in range(2):  # one retry if a concurrent bind raced us
            with app_state.session_factory() as session:
                try:
                    handle_message(session, wa, msg, now)
                    session.commit()
                    break
                except IntegrityError:
                    session.rollback()
                    if attempt:
                        raise


@router.post(PATH)
async def receive(request: Request) -> Response:
    state = request.app.state
    wa = state.settings.whatsapp
    if not wa.webhook_ready or state.session_factory is None:
        return Response(status_code=503)  # G02: not configured, never half-accept
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return Response(status_code=413)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return Response(status_code=413)
    if not signature_valid(wa.app_secret, raw, request.headers.get("x-hub-signature-256")):
        return Response(status_code=401)
    try:
        doc = json.loads(raw)
        messages, statuses = parse_messages(doc), parse_statuses(doc)
    except (ValueError, KeyError, TypeError, AttributeError, PayloadError):
        return Response(status_code=400)
    if business_ids(doc) - {(wa.waba_id, wa.phone_number_id)}:
        return Response(status_code=200)  # another business number: acknowledge, ignore
    await run_in_threadpool(_process, state, messages, datetime.now(UTC), statuses)
    return Response(status_code=200)
