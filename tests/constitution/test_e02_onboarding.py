"""E02 invite -> signed webhook -> exactly one tenant -> welcome (RC02, RC08, RC11).

Replay, parallel delivery, expired / forwarded / used invites, wrong sender, wrong
business number and bad signatures must never leak information or duplicate anything.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from desk.app import create_app
from desk.db.models import AgentBinding, Inbound, Invite, MessageBody, Outbox, Tenant
from desk.onboarding import messages as msgs
from desk.onboarding.invites import create_invite
from desk.transport.whatsapp.webhook import _process
from tests.whatsapp_helpers import inbound, message, payload, post

ALICE, BOB, EVE = "919800000001", "919800000002", "919800000003"


@pytest.fixture
def client(settings, db):
    return TestClient(create_app(settings=settings, session_factory=db))


def make_invite(db, wa, *, bound=None, with_code=True, ttl=timedelta(hours=72), now=None):
    with db() as s:
        inv, code = create_invite(
            s,
            business_phone_id=wa.phone_number_id,
            now=now or datetime.now(UTC),
            ttl=ttl,
            bound_sender=bound,
            with_code=with_code,
        )
        s.commit()
        return inv.id, code


def count(db, model, *where):
    with db() as s:
        return s.execute(select(func.count()).select_from(model).where(*where)).scalar()


def outbox(db, recipient=None):
    with db() as s:
        q = select(Outbox).order_by(Outbox.created_at, Outbox.kind)
        if recipient:
            q = q.where(Outbox.recipient == recipient)
        return list(s.execute(q).scalars())


# ---- webhook authentication -------------------------------------------------------------


def test_get_verify_returns_challenge_only_for_right_token(client, wa_settings):
    ok = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": wa_settings.verify_token,
            "hub.challenge": "12345",
        },
    )
    assert ok.status_code == 200 and ok.text == "12345"
    for params in (
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {},
    ):
        assert client.get("/webhooks/whatsapp", params=params).status_code == 403


@pytest.mark.parametrize(
    "case", ["missing", "wrong_secret", "garbage", "tampered", "verify_token_as_signature"]
)
def test_bad_signature_is_rejected_before_anything_is_stored(client, db, wa_settings, case):
    _, code = make_invite(db, wa_settings)
    body = payload(wa_settings, message(ALICE, f"Hi {code}"))
    kwargs = {
        "missing": {"signature": ""},
        "wrong_secret": {"secret": "not-the-app-secret"},
        "garbage": {"signature": "sha256=deadbeef"},
        "verify_token_as_signature": {"signature": wa_settings.verify_token},
        "tampered": {},
    }[case]
    sent = body.replace(ALICE.encode(), EVE.encode()) if case == "tampered" else body
    if case == "tampered":  # signature computed over the original body
        from desk.transport.whatsapp.signature import expected_signature

        kwargs = {"signature": expected_signature(wa_settings.app_secret, body)}
    r = post(client, wa_settings, sent, **kwargs)
    assert r.status_code == 401 and r.content == b""
    assert count(db, Inbound) == 0 and count(db, Tenant) == 0 and count(db, Outbox) == 0


def test_oversized_body_is_refused(client, wa_settings):
    huge = b"{" + b" " * (300 * 1024) + b"}"
    assert post(client, wa_settings, huge).status_code == 413


def test_unconfigured_webhook_refuses_instead_of_half_accepting(db):
    from desk.config import Settings

    app = create_app(
        settings=Settings(
            mode="mock", database_url=None, model_adapter="mock", feed_adapter="fixture"
        ),
        session_factory=db,
    )
    assert TestClient(app).post("/webhooks/whatsapp", content=b"{}").status_code == 503


def test_other_business_number_is_ignored(client, db, wa_settings):
    _, code = make_invite(db, wa_settings)
    for kw in ({"phone_number_id": "999"}, {"waba_id": "888"}):
        body = payload(wa_settings, message(ALICE, f"Hi {code}"), **kw)
        assert post(client, wa_settings, body).status_code == 200
    assert count(db, Tenant) == 0 and count(db, Inbound) == 0 and count(db, Outbox) == 0


# ---- happy path ---------------------------------------------------------------------------


def test_valid_invite_creates_one_tenant_binding_welcome_and_separate_opt_in(
    client, db, wa_settings
):
    invite_id, code = make_invite(db, wa_settings)
    r = post(client, wa_settings, payload(wa_settings, message(ALICE, f"Hi {code}")))
    assert r.status_code == 200
    with db() as s:
        (tenant,) = s.execute(select(Tenant)).scalars()
        assert (tenant.sender, tenant.state, tenant.opt_in_state) == (ALICE, "pending", "asked")
        assert not tenant.report_opt_in  # chat/invite does not authorise daily sends
        binding = s.get(AgentBinding, tenant.id)
        assert binding.chief_identity.endswith(str(tenant.id)) and binding.vm_ids is None
        invite = s.get(Invite, invite_id)
        assert invite.state == "consumed" and invite.consumed_by == tenant.id
        body = s.execute(select(MessageBody)).scalar_one()
        assert code not in body.body_redacted and "[invite code]" in body.body_redacted
    kinds = [o.kind for o in outbox(db, ALICE)]
    assert sorted(kinds) == ["opt_in", "welcome"]
    welcome = next(o for o in outbox(db) if o.kind == "welcome")
    assert "trades place nahi karta" in welcome.body and "pending" in welcome.body
    assert all(o.state == "PENDING" for o in outbox(db))  # M2 queues; M3 sends


def test_preapproved_sender_can_just_say_hi(client, db, wa_settings):
    make_invite(db, wa_settings, bound=BOB, with_code=False)
    post(client, wa_settings, payload(wa_settings, message(BOB, "Hi")))
    assert count(db, Tenant, Tenant.sender == BOB) == 1


def test_identity_comes_from_transport_not_display_name_or_text(client, db, wa_settings):
    make_invite(db, wa_settings, bound=BOB, with_code=False)
    # EVE claims to be Bob by display name and by typing Bob's number.
    body = payload(wa_settings, message(EVE, f"Hi, I am {BOB}", profile_name="Bob"))
    post(client, wa_settings, body)
    assert count(db, Tenant) == 0
    assert [o.kind for o in outbox(db, EVE)] == ["neutral"]


# ---- replay / parallel --------------------------------------------------------------------


def test_replayed_webhook_creates_nothing_new(client, db, wa_settings):
    _, code = make_invite(db, wa_settings)
    body = payload(wa_settings, message(ALICE, f"Hi {code}", msg_id="wamid.REPLAY1"))
    for _ in range(3):
        assert post(client, wa_settings, body).status_code == 200
    assert count(db, Tenant) == 1 and count(db, Inbound) == 1 and count(db, Outbox) == 2


def test_parallel_deliveries_bind_once_and_welcome_once(db, wa_settings):
    _, code = make_invite(db, wa_settings)
    state = SimpleNamespace(settings=SimpleNamespace(whatsapp=wa_settings), session_factory=db)
    barrier = threading.Barrier(8)

    def deliver(i):
        barrier.wait()
        _process(
            state, [inbound(wa_settings, ALICE, f"Hi {code}", f"wamid.PAR{i}")], datetime.now(UTC)
        )

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(deliver, range(8)))
    assert count(db, Tenant) == 1 and count(db, AgentBinding) == 1
    assert count(db, Outbox, Outbox.kind == "welcome") == 1
    assert count(db, Inbound) == 8


def test_two_people_racing_for_one_code_only_one_wins(db, wa_settings):
    _, code = make_invite(db, wa_settings)
    state = SimpleNamespace(settings=SimpleNamespace(whatsapp=wa_settings), session_factory=db)
    barrier = threading.Barrier(2)

    def deliver(sender):
        barrier.wait()
        _process(
            state,
            [inbound(wa_settings, sender, f"Hi {code}", f"wamid.R{sender}")],
            datetime.now(UTC),
        )

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(deliver, [ALICE, BOB]))
    assert count(db, Tenant) == 1
    assert count(db, Outbox, Outbox.kind == "welcome") == 1
    assert count(db, Outbox, Outbox.kind == "neutral") == 1


# ---- invalid invites: one neutral answer, no oracle -------------------------------------


def _neutral_reply_for(client, db, wa, sender, text):
    post(client, wa, payload(wa, message(sender, text)))
    return [o.body for o in outbox(db, sender)]


def test_every_invalid_invite_case_gets_the_same_neutral_reply(client, db, wa_settings):
    past = datetime.now(UTC) - timedelta(days=5)
    _, expired = make_invite(db, wa_settings, now=past, ttl=timedelta(days=1))
    _, forwarded = make_invite(db, wa_settings, bound=ALICE)  # bound to Alice
    _, used = make_invite(db, wa_settings)
    post(client, wa_settings, payload(wa_settings, message(ALICE, f"Hi {used}")))

    replies = [
        _neutral_reply_for(client, db, wa_settings, "919800000011", f"Hi {expired}"),
        _neutral_reply_for(client, db, wa_settings, "919800000012", f"Hi {forwarded}"),
        _neutral_reply_for(client, db, wa_settings, "919800000013", f"Hi {used}"),
        _neutral_reply_for(
            client, db, wa_settings, "919800000014", "Hi PX-AAAAAAAAAAAAAAAAAAAAAAAAAA"
        ),
        _neutral_reply_for(client, db, wa_settings, "919800000015", "Hi"),
    ]
    assert replies == [[msgs.NEUTRAL]] * 5
    assert count(db, Tenant) == 1  # only Alice
    assert ALICE not in msgs.NEUTRAL
    with db() as s:  # non-tenants' message text is not stored at all
        assert s.execute(select(func.count()).select_from(MessageBody)).scalar() == 1


def test_neutral_reply_at_most_once_per_day(client, db, wa_settings):
    for _ in range(3):
        post(client, wa_settings, payload(wa_settings, message(EVE, "Hi")))
    assert [o.kind for o in outbox(db, EVE)] == ["neutral"]
    assert count(db, Inbound, Inbound.sender == EVE) == 3


def test_bound_tenant_repeating_invite_gets_no_second_welcome(client, db, wa_settings):
    _, code = make_invite(db, wa_settings)
    post(client, wa_settings, payload(wa_settings, message(ALICE, f"Hi {code}")))
    post(client, wa_settings, payload(wa_settings, message(ALICE, f"Hi {code}")))
    assert count(db, Outbox, Outbox.kind == "welcome") == 1
    assert count(db, Tenant) == 1


# ---- resume, opt-in, STOP / START ---------------------------------------------------------


def _bind(client, db, wa):
    _, code = make_invite(db, wa)
    post(client, wa, payload(wa, message(ALICE, f"Hi {code}")))


def _say(client, wa, text):
    post(client, wa, payload(wa, message(ALICE, text)))


def _tenant(db):
    with db() as s:
        return s.execute(select(Tenant)).scalar_one()


def test_opt_in_is_separate_and_explicit(client, db, wa_settings):
    _bind(client, db, wa_settings)
    assert _tenant(db).opt_in_state == "asked"
    _say(client, wa_settings, "yes")
    assert _tenant(db).report_opt_in


def test_stop_pauses_daily_but_questions_still_work_and_start_reconfirms(client, db, wa_settings):
    _bind(client, db, wa_settings)
    _say(client, wa_settings, "YES")
    _say(client, wa_settings, "STOP")
    assert _tenant(db).opt_in_state == "stopped" and not _tenant(db).report_opt_in
    _say(client, wa_settings, "Aaj ke main catalysts?")
    assert outbox(db, ALICE)[-1].kind == "question_pending"
    _say(client, wa_settings, "yes")  # YES after STOP without START is just a question
    assert not _tenant(db).report_opt_in
    _say(client, wa_settings, "START")
    assert _tenant(db).opt_in_state == "asked"
    _say(client, wa_settings, "haan")
    assert _tenant(db).report_opt_in
    kinds = [o.kind for o in outbox(db, ALICE)]
    assert kinds.count("welcome") == 1 and "stop" in kinds and "start" in kinds


def test_question_while_setup_pending_is_answered_honestly(client, db, wa_settings):
    _bind(client, db, wa_settings)
    _say(client, wa_settings, "Nifty ka view?")
    reply = outbox(db, ALICE)[-1]
    assert reply.kind == "question_pending" and "pending" in reply.body
    assert "G01" in reply.body  # names the real reason, no fake live agent


def test_voice_note_gets_text_only_notice(client, db, wa_settings):
    _bind(client, db, wa_settings)
    post(client, wa_settings, payload(wa_settings, message(ALICE, None, msg_type="audio")))
    assert outbox(db, ALICE)[-1].kind == "text_only"
