"""E08 STOP/START, optional 09:12 auction addendum, follow-up text version, feedback and
corrections (RC10, RC11). Visual media checks are in test_d03_media.py; the human UAT
(D07) stays with the owner."""

import itertools

import pytest
from sqlalchemy import select

from desk.core.lens import LensId
from desk.db.models import Correction, Feedback, Outbox, StoredReport, Tenant
from desk.feedback import CorrectionError, issue_correction
from desk.jobs.scheduler import plan_day
from desk.jobs.worker import run_one
from desk.onboarding.service import handle_message
from desk.outbox.addendum import CAVEAT
from desk.outbox.sender import send_batch
from desk.transport.whatsapp.client import FakeGraph
from desk.transport.whatsapp.payload import InboundMessage
from tests.conftest import MOCK_DAY, feed
from tests.delivery_helpers import DRAFTS, Clock, add_tenant, deps, ist

ALICE, BOB = "919800000001", "919800000002"
_ids = itertools.count(1)


def say(db, wa, sender, text, at):
    msg = InboundMessage(
        f"wamid.E08-{next(_ids)}", wa.waba_id, wa.phone_number_id, sender, at, "text", text
    )
    with db() as s:
        out = handle_message(s, wa, msg, at)
        s.commit()
    return out


def rows(db, **filters):
    with db() as s:
        q = select(Outbox).order_by(Outbox.created_at, Outbox.part_no, Outbox.idempotency_key)
        for k, v in filters.items():
            q = q.where(getattr(Outbox, k) == v)
        return list(s.execute(q).scalars())


def morning(db, cal, scenario="full_mock", auction=False, at=None):
    clock = Clock(at or ist(8, 0))
    with db() as s:
        plan_day(s, cal, MOCK_DAY, ist(7, 0), auction_enabled=auction)
        s.commit()
    assert run_one(db, deps(clock, cal, feed_for=lambda d: feed(scenario)), "w1") == "done"
    return clock


# ---- STOP / START through real deliveries -----------------------------------------------


def test_stop_and_start_control_who_gets_the_next_report(db, mock_calendar, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    say(db, wa_settings, ALICE, "STOP", ist(7, 5))
    morning(db, mock_calendar)
    assert rows(db, kind="report_part") == []
    say(db, wa_settings, ALICE, "START", ist(8, 10))
    say(db, wa_settings, ALICE, "YES", ist(8, 11))
    with db() as s:
        assert s.execute(select(Tenant)).scalar_one().report_opt_in


# ---- 09:12 addendum ---------------------------------------------------------------------


def test_auction_addendum_is_labelled_linked_and_sent_before_open(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar, "auction_mock", auction=True)
    clock = Clock(ist(9, 10))
    result = run_one(db, deps(clock, mock_calendar, feed_for=lambda d: feed("auction_mock")), "w1")
    assert result == "done:addendum"
    (add,) = rows(db, kind="addendum")
    for must in (
        "INDICATIVE AUCTION",
        "as of 01 Oct 2026 09:12 IST",
        "Update to MOCK-2026-10-01-MORNING v1",
        "4 items: INDICATIVE AUCTION unless marked | as of 01 Oct 2026 09:08 IST | "
        "MOCK feed: pre_open",
        "- MOCKBANK_A indicative equilibrium price: 1658.00 INR",
        CAVEAT,
    ):
        assert must in add.body, must
    assert add.expires_at == ist(9, 15)
    assert add.body.count("MOCK feed: pre_open") == 1  # provenance printed once
    graph = FakeGraph()
    send_batch(db, graph.client(), DRAFTS, ist(9, 11))
    assert rows(db, kind="addendum")[0].state == "ACCEPTED"


def test_addendum_after_the_open_is_cancelled_not_sent(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar, "auction_mock", auction=True)
    run_one(
        db, deps(Clock(ist(9, 10)), mock_calendar, feed_for=lambda d: feed("auction_mock")), "w1"
    )
    graph = FakeGraph()
    with db() as s:  # morning parts already gone; only the addendum is left
        for r in s.execute(select(Outbox).where(Outbox.kind == "report_part")).scalars():
            r.state = "DELIVERED"
        s.commit()
    send_batch(db, graph.client(), DRAFTS, ist(9, 16))
    (add,) = rows(db, kind="addendum")
    assert add.state == "CANCELLED" and "expired" in add.error and graph.requests == []


def test_addendum_without_auction_data_says_what_is_unknown(db, mock_calendar):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar, auction=True)
    run_one(db, deps(Clock(ist(9, 10)), mock_calendar), "w1")  # full_mock: no pre_open
    (add,) = rows(db, kind="addendum")
    assert "- nothing: no indicative auction values available" in add.body
    assert "Still unknown:" in add.body and "Pre-open data" in add.body


# ---- follow-up: TEXT version ----------------------------------------------------------------


def test_text_alone_returns_the_section_list(db, mock_calendar, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar)
    assert say(db, wa_settings, ALICE, "text", ist(8, 50)).handled_as == "text_index"
    (index,) = rows(db, kind="text_index")
    assert "TEXT index" in index.body.splitlines()[0]
    assert "Reply TEXT R05" in index.body and "TEXT ALL" in index.body
    for lens in ("R01 Overnight news", "R08 Order flow", "R15 Data / quality summary"):
        assert lens in index.body


def test_text_r05_returns_one_labelled_section(db, mock_calendar, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar)
    assert say(db, wa_settings, ALICE, "TEXT R05", ist(8, 50)).handled_as == "text_R05"
    (part,) = rows(db, kind="text_section")
    first = part.body.splitlines()[0]
    assert "MOCK-2026-10-01-MORNING v1" in first and first.endswith("TEXT R05")
    assert "R05 Large-cap focus" in part.body and "R06" not in part.body
    assert len(part.body) <= 4096


def test_chart_text_version_lives_in_text_r04_not_the_caption(db, mock_calendar, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar)
    chart = [p for p in rows(db, kind="report_part") if p.part_no == 3][0]
    assert "Text version: reply TEXT R04" in chart.body and "up" not in chart.body.split()
    say(db, wa_settings, ALICE, "TEXT R04", ist(8, 50))
    (part,) = rows(db, kind="text_section")
    assert "Chart in words:" in part.body and "Auto +1.05% up" in part.body


def test_text_all_returns_full_report_as_labelled_text_parts(db, mock_calendar, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar)
    assert say(db, wa_settings, ALICE, "TEXT ALL", ist(8, 50)).handled_as == "text_all"
    parts = rows(db, kind="text_part")
    n = len(parts)
    assert n >= 3 and not any(p.proactive for p in parts)
    joined = "\n".join(p.body for p in parts)
    for i, p in enumerate(parts, 1):
        assert f"part {i}/{n}" in p.body.splitlines()[0] and len(p.body) <= 4096
    for lens in ("R01 Overnight news", "R13 Options lens", "R15 Data / quality summary"):
        assert lens in joined


def test_text_command_without_todays_report_says_so(db, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    assert say(db, wa_settings, ALICE, "TEXT", ist(8, 50)).handled_as == "text_none"


# ---- feedback ---------------------------------------------------------------------------


def test_minimal_feedback_is_stored_linked_and_acknowledged(db, mock_calendar, wa_settings):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    morning(db, mock_calendar)
    say(db, wa_settings, ALICE, "useful", ist(9, 0))
    say(db, wa_settings, ALICE, "NOT USEFUL", ist(9, 1))
    say(db, wa_settings, ALICE, "Feedback: R13 too long PX-AAAAAAAAAAAAAAAAAAAAAAAAAA", ist(9, 2))
    with db() as s:
        fb = s.execute(select(Feedback).order_by(Feedback.created_at)).scalars().all()
    assert [f.kind for f in fb] == ["useful", "not_useful", "text"]
    assert all(f.report_id == "MOCK-2026-10-01-MORNING" for f in fb)
    assert "PX-" not in fb[2].text_redacted and "R13 too long" in fb[2].text_redacted
    assert len(rows(db, kind="feedback_ack")) == 3


# ---- corrections ------------------------------------------------------------------------


def _delivered_to_alice(db, cal):
    add_tenant(db, ALICE, "yes", ist(7, 0))
    add_tenant(db, BOB, "no", ist(7, 0))
    clock = morning(db, cal)
    send_batch(db, FakeGraph().client(), DRAFTS, clock.now)
    return clock


def _correct(db, **over):
    kw = dict(
        report_id="MOCK-2026-10-01-MORNING",
        from_version=1,
        lens=LensId.R04,
        previous_claim="Banks return +0.82%",
        corrected_claim="Banks return +0.62%",
        source_url="https://example.invalid/mock/sectors-revised",
        source_time=ist(9, 30),
        scenario_impact="none: base/up/down unchanged",
        reason="provider revised sector close",
        now=ist(9, 40),
    )
    kw.update(over)
    with db() as s:
        corr = issue_correction(s, **kw)
        s.commit()
        return corr


def test_correction_names_claim_source_versions_and_keeps_original(db, mock_calendar):
    _delivered_to_alice(db, mock_calendar)
    with db() as s:
        original = s.execute(select(StoredReport)).scalar_one()
        before = (original.content_hash, dict(original.body))
    corr = _correct(db)
    assert (corr.from_version, corr.new_version) == (1, 2)
    (msg,) = rows(db, kind="correction")
    assert msg.recipient == ALICE  # only people who were sent the original
    for must in (
        "CORRECTION | MOCK-2026-10-01-MORNING v1 -> v2",
        "section R04 Sector map",
        "Previous claim: Banks return +0.82%",
        "Verified change: Banks return +0.62%",
        "https://example.invalid/mock/sectors-revised",
        "Scenario impact: none",
        "original v1 stays on record unchanged",
    ):
        assert must in msg.body, must
    with db() as s:
        original = s.execute(select(StoredReport)).scalar_one()
        assert (original.content_hash, original.body) == before
        assert s.execute(select(Correction)).scalar_one().original_hash == before[0]
    assert _correct(db, corrected_claim="Banks return +0.63%").new_version == 3


def test_correction_can_be_sent_on_a_later_day_and_says_its_date(db, mock_calendar, wa_settings):
    _delivered_to_alice(db, mock_calendar)
    _correct(db, now=ist(8, 0, day=5))
    say(db, wa_settings, ALICE, "ok", ist(8, 1, day=5))  # window open on 5 Oct
    send_batch(db, FakeGraph().client(), DRAFTS, ist(8, 2, day=5))
    (msg,) = rows(db, kind="correction")
    assert msg.state == "ACCEPTED" and "01 Oct 2026" in msg.body


@pytest.mark.parametrize(
    "bad",
    [
        {"source_url": "http://insecure.example"},
        {"report_id": "NO-SUCH-REPORT"},
        {"from_version": 9},
    ],
)
def test_correction_needs_a_real_report_and_verifiable_source(db, mock_calendar, bad):
    _delivered_to_alice(db, mock_calendar)
    with pytest.raises(CorrectionError):
        _correct(db, **bad)


def test_correction_respects_stop(db, mock_calendar, wa_settings):
    _delivered_to_alice(db, mock_calendar)
    say(db, wa_settings, ALICE, "STOP", ist(9, 35))
    _correct(db)
    send_batch(db, FakeGraph().client(), DRAFTS, ist(9, 45))
    assert rows(db, kind="correction")[0].state == "CANCELLED"
