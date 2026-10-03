"""E05 scheduled report -> review -> outbox -> accepted / delivered / failed states;
outside the 24h window: approved template or a safe wait (RC10, RC11).

Nothing here touches the network: the Graph API is an in-memory FakeGraph (G02 BLOCKED).
"""

import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from desk.app import create_app
from desk.config import GateBlockedError, Settings
from desk.db.models import DeliveryReceipt, Outbox, Tenant
from desk.jobs.scheduler import plan_day
from desk.jobs.worker import run_one
from desk.outbox.policy import TEMPLATE_KIND
from desk.outbox.receipts import apply_status
from desk.outbox.sender import MAX_SEND_ATTEMPTS, SENDING_GRACE, claim, send_batch
from desk.transport.whatsapp.client import FakeGraph, make_client
from desk.transport.whatsapp.payload import StatusUpdate
from tests.conftest import MOCK_DAY
from tests.delivery_helpers import DRAFTS, ONE_HOUR, Clock, add_tenant, deps, ist, registry
from tests.whatsapp_helpers import post

ALICE, BOB, CAROL, DAVE = "919800000001", "919800000002", "919800000003", "919800000004"


def produce_report(db, mock_calendar, at=None):
    clock = Clock(at or ist(8, 0))
    with db() as s:
        plan_day(s, mock_calendar, MOCK_DAY, clock.now)
        s.commit()
    assert run_one(db, deps(clock, mock_calendar), "w1").startswith("done")
    return clock


def rows(db, **filters):
    with db() as s:
        q = select(Outbox).order_by(Outbox.created_at, Outbox.part_no)
        for k, v in filters.items():
            q = q.where(getattr(Outbox, k) == v)
        return list(s.execute(q).scalars())


def status(provider_id, st, recipient, callback=None, code=None):
    from datetime import UTC, datetime

    return StatusUpdate(
        provider_id, st, datetime.now(UTC), recipient, callback, code, "err" if code else None
    )


# ---- what gets queued -------------------------------------------------------------------


def test_report_is_queued_only_for_opted_in_tenants(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    for sender, state in ((BOB, "asked"), (CAROL, "no"), (DAVE, "stopped")):
        add_tenant(db, sender, state, ist(7, 0))
    produce_report(db, mock_calendar)
    assert {r.recipient for r in rows(db)} == {ALICE}


def test_every_part_is_self_labelled_and_fits_whatsapp(db, mock_calendar):
    from desk.db.models import Media
    from tests.media_helpers import pdf_text

    add_tenant(db, ALICE, "yes", ist(7, 0))
    produce_report(db, mock_calendar)
    parts = rows(db, kind="report_part")
    assert len(parts) == 3  # summary text, full PDF, sector chart
    for i, p in enumerate(parts, 1):
        first_line = p.body.splitlines()[0]
        for must in (
            "MOCK",
            "MOCK-2026-10-01-MORNING v1",
            "01 Oct 2026",
            "as of 01 Oct 2026 08:45 IST",
            f"part {i}/3",
        ):
            assert must in first_line, (i, must)
        assert p.part_no == i and p.part_total == 3 and p.payload_hash and p.proactive
    summary, pdf_part, chart_part = parts
    assert summary.media_id is None and len(summary.body) <= 4096
    assert summary.body.index("SESSION PATHS") < summary.body.index("DATA GAPS")
    assert "R01 Overnight news" not in summary.body  # gaps first; full report follows
    assert "Audio summary: UNAVAILABLE" in summary.body
    with db() as s:
        pdf = s.get(Media, pdf_part.media_id)
        png = s.get(Media, chart_part.media_id)
        assert (pdf.kind, pdf.mime, png.kind, png.mime) == (
            "document",
            "application/pdf",
            "image",
            "image/png",
        )
        assert len(pdf_part.body) <= 1024 and len(chart_part.body) <= 1024
        text = pdf_text(pdf.content)
    for lens in ("R01 Overnight news", "R08 Order flow", "R15 Data / quality summary"):
        assert lens in text


# ---- inside the window ------------------------------------------------------------------


def test_window_open_parts_are_accepted_then_receipts_move_forward_only(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    clock = produce_report(db, mock_calendar)
    graph = FakeGraph()
    stats = send_batch(db, graph.client(), DRAFTS, clock.now)
    parts = rows(db, kind="report_part")
    assert stats.counts == {"ACCEPTED": len(parts)}
    assert all(r.state == "ACCEPTED" and r.provider_message_id for r in parts)
    assert [g["type"] for g in graph.requests] == ["text", "document", "image"]
    assert len(graph.uploads) == 2  # PDF + PNG uploaded first, then sent by media id
    assert [g["biz_opaque_callback_data"] for g in graph.requests] == [str(r.id) for r in parts]

    pid = parts[0].provider_message_id
    with db() as s:
        for st in ("sent", "read", "delivered", "read"):  # out of order + duplicate
            apply_status(s, status(pid, st, ALICE), clock.now)
        s.commit()
        assert s.get(Outbox, parts[0].id).state == "READ"
        assert s.execute(select(func.count()).select_from(DeliveryReceipt)).scalar() == 3


def test_failed_receipt_is_recorded_as_failed(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    clock = produce_report(db, mock_calendar)
    send_batch(db, FakeGraph().client(), DRAFTS, clock.now)
    part = rows(db, kind="report_part")[0]
    with db() as s:
        apply_status(s, status(part.provider_message_id, "failed", ALICE, code="131026"), clock.now)
        s.commit()
        row = s.get(Outbox, part.id)
        assert row.state == "FAILED" and "131026" in row.error


# ---- outside the window -----------------------------------------------------------------


def test_outside_window_without_approved_template_waits_safely(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0) - timedelta(hours=30))
    clock = produce_report(db, mock_calendar)
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert graph.requests == []
    for r in rows(db, kind="report_part"):
        assert r.state == "WAITING_WINDOW"
        assert "outside 24h window" in r.wait_reason and "DRAFT" in r.wait_reason
        assert "G02" in r.wait_reason


def test_customer_reply_releases_waiting_parts(db, mock_calendar, wa_settings):
    from desk.onboarding.service import handle_message
    from desk.transport.whatsapp.payload import InboundMessage

    add_tenant(db, ALICE, "yes", ist(7, 0) - timedelta(hours=30))
    clock = produce_report(db, mock_calendar)
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, clock.now)
    clock.now += timedelta(minutes=5)
    msg = InboundMessage(
        "wamid.REPLY",
        wa_settings.waba_id,
        wa_settings.phone_number_id,
        ALICE,
        clock.now,
        "text",
        "Aaj ka brief?",
    )
    with db() as s:
        handle_message(s, wa_settings, msg, clock.now)
        s.commit()
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert {r.state for r in rows(db, kind="report_part")} == {"ACCEPTED"}


def test_outside_window_with_approved_template_sends_it_once_then_waits(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0) - timedelta(hours=30))
    clock = produce_report(db, mock_calendar)
    graph = FakeGraph()
    approved = registry("APPROVED")
    send_batch(db, graph.client(), approved, clock.now)  # parts wait; template queued
    send_batch(db, graph.client(), approved, clock.now)  # template goes out
    send_batch(db, graph.client(), approved, clock.now)  # nothing new
    assert [g["type"] for g in graph.requests] == ["template"]
    assert graph.requests[0]["template"]["name"] == "report_ready_v1"
    assert len(rows(db, kind=TEMPLATE_KIND)) == 1
    assert {r.state for r in rows(db, kind="report_part")} == {"WAITING_WINDOW"}


@pytest.mark.parametrize("tpl_status", ["PAUSED", "REJECTED", "PENDING"])
def test_paused_or_unapproved_template_pauses_proactive_sends(db, mock_calendar, tpl_status):
    add_tenant(db, ALICE, "yes", ist(7, 0) - timedelta(hours=30))
    clock = produce_report(db, mock_calendar)
    graph = FakeGraph()
    for _ in range(2):
        send_batch(db, graph.client(), registry(tpl_status), clock.now)
    assert graph.requests == []
    assert all(tpl_status in r.wait_reason for r in rows(db, kind="report_part"))


# ---- provider outcomes ------------------------------------------------------------------


def _one_part(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    clock = produce_report(db, mock_calendar)
    with db() as s:  # keep only part 1 pending to make counting simple
        for r in s.execute(select(Outbox).where(Outbox.part_no > 1)).scalars():
            r.state = "CANCELLED"
        s.commit()
    return clock


def test_timeout_after_sending_is_unknown_never_resent_then_reconciled(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [httpx.ReadTimeout("no answer")]
    send_batch(db, graph.client(), DRAFTS, clock.now)
    (part,) = rows(db, part_no=1)
    assert part.state == "UNKNOWN" and part.provider_message_id is None
    clock.now += ONE_HOUR
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert len(graph.requests) == 1  # never resent blindly
    with db() as s:  # receipt carries our callback (outbox id) -> reconcile
        apply_status(s, status("wamid.LATE1", "delivered", ALICE, callback=str(part.id)), clock.now)
        s.commit()
        row = s.get(Outbox, part.id)
        assert (row.state, row.provider_message_id) == ("DELIVERED", "wamid.LATE1")


def test_forged_callback_for_another_recipient_is_ignored(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [httpx.ReadTimeout("no answer")]
    send_batch(db, graph.client(), DRAFTS, clock.now)
    (part,) = rows(db, part_no=1)
    with db() as s:
        assert (
            apply_status(s, status("wamid.X", "read", BOB, callback=str(part.id)), clock.now)
            == "orphan_receipt"
        )
        s.commit()
    assert rows(db, part_no=1)[0].state == "UNKNOWN"


@pytest.mark.parametrize("step", [500, 503, b"not json"])
def test_server_errors_and_garbage_are_unknown(db, mock_calendar, step):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [step if isinstance(step, int) else httpx.RemoteProtocolError("bad")]
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert rows(db, part_no=1)[0].state == "UNKNOWN"


def test_connection_refused_is_retried_with_backoff(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [httpx.ConnectError("refused")]
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert rows(db, part_no=1)[0].state == "PENDING"
    send_batch(db, graph.client(), DRAFTS, clock.now)  # still inside backoff
    assert len(graph.requests) == 1
    clock.now += timedelta(minutes=1)
    send_batch(db, graph.client(), DRAFTS, clock.now)
    (part,) = rows(db, part_no=1)
    assert part.state == "ACCEPTED" and part.attempts == 2 and len(graph.requests) == 2


def test_rate_limit_retries_then_gives_up(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [{"error": {"code": 130429, "message": "rate"}}] * MAX_SEND_ATTEMPTS
    for _ in range(MAX_SEND_ATTEMPTS):
        send_batch(db, graph.client(), DRAFTS, clock.now)
        clock.now += timedelta(minutes=11)
    (part,) = rows(db, part_no=1)
    assert part.state == "FAILED" and "gave up" in part.error
    assert len(graph.requests) == MAX_SEND_ATTEMPTS


def test_permanent_rejection_fails_without_retry(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [{"error": {"code": 131026, "message": "undeliverable"}}]
    send_batch(db, graph.client(), DRAFTS, clock.now)
    clock.now += ONE_HOUR
    send_batch(db, graph.client(), DRAFTS, clock.now)
    (part,) = rows(db, part_no=1)
    assert part.state == "FAILED" and "131026" in part.error and len(graph.requests) == 1


def test_provider_says_window_closed_so_we_wait(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    graph = FakeGraph()
    graph.script = [{"error": {"code": 131047, "message": "re-engagement"}}]
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert rows(db, part_no=1)[0].state == "WAITING_WINDOW"


def test_crash_mid_send_becomes_unknown_not_resent(db, mock_calendar):
    clock = _one_part(db, mock_calendar)
    with db() as s:  # a sender claims the row, then dies before recording anything
        claim(s, clock.now, 10)
        s.commit()
    clock.now += SENDING_GRACE + timedelta(seconds=1)
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, clock.now)
    (part,) = rows(db, part_no=1)
    assert part.state == "UNKNOWN" and "not resent" in part.error and graph.requests == []


# ---- consent / staleness at send time ---------------------------------------------------


def test_stop_after_queueing_cancels_pending_parts(db, mock_calendar):
    tid = add_tenant(db, ALICE, "yes", ist(7, 0))
    clock = produce_report(db, mock_calendar)
    with db() as s:
        s.get(Tenant, tid).opt_in_state = "stopped"
        s.commit()
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, clock.now)
    assert graph.requests == []
    assert {r.state for r in rows(db, kind="report_part")} == {"CANCELLED"}


def test_yesterdays_report_is_never_sent_as_today(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    clock = produce_report(db, mock_calendar)
    with db() as s:  # customer writes next day: window open, report still queued
        t = s.execute(select(Tenant)).scalar_one()
        t.last_inbound_at = ist(8, 0, day=5)
        s.commit()
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, ist(8, 30, day=5))
    assert graph.requests == []
    for r in rows(db, kind="report_part"):
        assert r.state == "CANCELLED" and "stale" in r.error
    del clock


def test_reply_outside_window_is_cancelled_not_sent(db):
    from datetime import UTC, datetime

    tid = add_tenant(db, ALICE, "yes", ist(7, 0) - timedelta(hours=30))
    with db() as s:
        s.add(
            Outbox(
                id=uuid.uuid4(),
                idempotency_key="k1",
                tenant_id=tid,
                business_phone_id="100200300",
                recipient=ALICE,
                kind="question_pending",
                body="x",
                state="PENDING",
                created_at=datetime.now(UTC),
                attempts=0,
            )
        )
        s.commit()
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, ist(8, 0))
    assert graph.requests == [] and rows(db)[0].state == "CANCELLED"


# ---- transport safety -------------------------------------------------------------------


def test_only_mock_sending_exists_while_g02_is_blocked(settings):
    client = make_client(settings)
    assert client.send({"to": ALICE}).result == "ACCEPTED"  # in-memory FakeGraph
    live = Settings(mode="live", database_url=None, model_adapter="mock", feed_adapter="fixture")
    with pytest.raises(GateBlockedError):
        make_client(live)


def test_signed_status_webhook_updates_delivery(db, settings, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    clock = produce_report(db, mock_calendar)
    send_batch(db, FakeGraph().client(), DRAFTS, clock.now)
    part = rows(db, part_no=1)[0]
    wa = settings.whatsapp
    import json

    doc = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": wa.waba_id,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": wa.phone_number_id},
                            "statuses": [
                                {
                                    "id": part.provider_message_id,
                                    "status": "delivered",
                                    "timestamp": "1790000000",
                                    "recipient_id": ALICE,
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    client = TestClient(create_app(settings=settings, session_factory=db))
    assert post(client, wa, json.dumps(doc).encode()).status_code == 200
    assert rows(db, part_no=1)[0].state == "DELIVERED"
    wrong_key = "x" * 64  # a wrong app secret: the signature must not verify
    assert post(client, wa, json.dumps(doc).encode(), secret=wrong_key).status_code == 401
