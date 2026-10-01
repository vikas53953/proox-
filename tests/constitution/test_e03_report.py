"""E03 dated market fixture -> R01-R15 report.

RC03 every fact has source / as-of / instrument / unit.
RC04 every lens accounted for, with honest UNAVAILABLE / STALE status.
RC07 no metric without the right to it (order flow, GIFT Nifty); N/A is never zero.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from desk.agents.reviewer import ReviewStatus, review
from desk.core.facts import DataClass, Fact, Gap, Source
from desk.core.lens import LensId, LensResult, LensStatus
from desk.feeds.base import Dataset, DatasetKind
from desk.lenses.context import ReportKind
from desk.pipeline import NoReport
from desk.report.assemble import assemble
from desk.report.model import IncompleteReportError
from desk.report.text import render_text
from tests.conftest import MOCK_DAY, feed

SRC = Source(id="s", name="MOCK", url="https://example.invalid/s", is_mock=True)
TS = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


def lens(report, lid: LensId) -> LensResult:
    return next(r for r in report.lenses if r.lens is lid)


# ---- RC04 completeness -----------------------------------------------------------------


def test_full_mock_accounts_for_all_15_lenses_in_order(build_report):
    report = build_report()
    assert [r.lens for r in report.lenses] == list(LensId)
    for r in report.lenses:
        assert r.facts or r.gaps, f"{r.lens} is silent"


def test_assembly_refuses_a_missing_lens(build_report):
    report = build_report()
    without_r08 = [r for r in report.lenses if r.lens is not LensId.R08]
    with pytest.raises(IncompleteReportError, match="R08"):
        assemble(
            kind=report.kind,
            trading_date=report.trading_date,
            cutoff=report.cutoff,
            version=1,
            is_mock=True,
            feed="x",
            model="x",
            calendar_version="x",
            method_versions={},
            lenses=without_r08,
            scenarios=report.scenarios,
            review=review(without_r08, report.scenarios, report.cutoff, True),
        )


def test_lens_must_report_facts_or_gaps():
    with pytest.raises(ValidationError):
        LensResult(lens=LensId.R01)


def test_lens_status_is_computed_not_asserted():
    fact = Fact(
        label="x",
        value=Decimal(1),
        unit="u",
        instrument="i",
        source=SRC,
        as_of=TS,
        data_class=DataClass.PRIOR_SESSION,
    )
    gap = Gap(topic="t", data_class=DataClass.UNAVAILABLE, reason="no rights")
    na = Gap(topic="t", data_class=DataClass.NOT_APPLICABLE, reason="pre-open")
    assert LensResult(lens=LensId.R01, facts=(fact,)).status is LensStatus.COMPLETE
    assert LensResult(lens=LensId.R01, facts=(fact,), gaps=(na,)).status is LensStatus.COMPLETE
    assert LensResult(lens=LensId.R01, facts=(fact,), gaps=(gap,)).status is LensStatus.DEGRADED
    assert LensResult(lens=LensId.R01, gaps=(gap,)).status is LensStatus.UNAVAILABLE


# ---- RC03 every fact sourced -----------------------------------------------------------


@pytest.mark.parametrize(
    "scenario", ["full_mock", "stale_quote", "no_orderflow_rights", "prior_session_stale"]
)
def test_every_fact_has_source_asof_instrument_unit(build_report, scenario):
    report = build_report(scenario)
    for r in report.lenses:
        for f in r.facts:
            assert f.source.url.startswith("https://"), f.label
            assert f.as_of.tzinfo is not None and f.as_of <= report.cutoff, f.label
            assert f.instrument.strip() and f.unit.strip(), f.label
            assert f.data_class not in (DataClass.UNAVAILABLE, DataClass.NOT_APPLICABLE)


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": " "},
        {"instrument": ""},
        {"as_of": datetime(2026, 9, 30, 10, 0)},  # naive timestamp  # noqa: DTZ001
        {"data_class": DataClass.UNAVAILABLE},  # missing data can't carry a value
        {"data_class": DataClass.PROXY},  # proxy without method/limits note
        {"data_class": DataClass.STALE},  # stale without age
        {"value": 1.5},  # float, not Decimal
    ],
)
def test_fact_constructor_enforces_rc03(bad):
    base = dict(
        label="x",
        value=Decimal(1),
        unit="u",
        instrument="i",
        source=SRC,
        as_of=TS,
        data_class=DataClass.PRIOR_SESSION,
    )
    with pytest.raises(ValidationError):
        Fact(**(base | bad))


def test_gap_has_no_value_field_so_na_cannot_become_zero():
    assert "value" not in Gap.model_fields
    with pytest.raises(ValidationError):
        Gap(topic="t", data_class=DataClass.PRIOR_SESSION, reason="x")
    with pytest.raises(ValidationError):
        Gap(topic="t", data_class=DataClass.UNAVAILABLE, reason=" ")


# ---- RC04/RC07 degraded, not fabricated ------------------------------------------------


def test_missing_orderflow_rights_gives_unavailable_not_a_number(build_report):
    report = build_report("no_orderflow_rights")
    r08 = lens(report, LensId.R08)
    assert r08.status is LensStatus.UNAVAILABLE
    assert r08.facts == ()
    assert any("no licensed trade-event feed" in g.reason for g in r08.gaps)
    section = render_text(report).split("R08 Order flow")[1].split("\nR09")[0]
    assert "UNAVAILABLE" in section
    assert not re.search(r"delta.*\d", section), "a delta number leaked into R08"


def test_price_only_trades_give_proxy_never_real_delta(build_report):
    r08 = lens(build_report(), LensId.R08)
    (fact,) = r08.facts
    assert fact.data_class is DataClass.PROXY
    assert "tick-rule" in fact.note and "not exchange aggressor flag" in fact.note


def test_stale_global_quote_is_labelled_with_age(build_report):
    report = build_report("stale_quote")
    r02 = lens(report, LensId.R02)
    assert r02.status is LensStatus.DEGRADED
    stale = [f for f in r02.facts if f.data_class is DataClass.STALE]
    assert stale and all("before cutoff" in f.note for f in stale)
    assert all("S&P 500" in f.instrument for f in stale)
    assert any(g.startswith("STALE") and "R02" in g for g in report.top_gaps)


def test_prior_session_file_from_wrong_day_is_never_shown_as_prior_session(build_report):
    r04 = lens(build_report("prior_session_stale"), LensId.R04)
    assert r04.status is LensStatus.DEGRADED
    assert all(f.data_class is DataClass.STALE for f in r04.facts)
    assert any("2026-09-29" in g.reason and "2026-09-30" in g.reason for g in r04.gaps)


def test_unlicensed_gift_nifty_is_not_used_even_if_feed_has_it(build_report, mock_calendar):
    """RC07: a dataset without the right to it is ignored, whatever the feed returns."""
    from desk.agents.model import MockModelAdapter
    from desk.pipeline import run_report

    base = feed("full_mock")

    class LeakyFeed:
        name, is_mock = "leaky", True

        def capabilities(self):
            return base.capabilities()

        def fetch(self, kind, day):
            if kind is DatasetKind.GIFT_NIFTY:
                return Dataset(
                    kind,
                    SRC,
                    datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
                    [{"contract": "OCT", "last": "25300", "delay_min": 0}],
                )
            return base.fetch(kind, day)

    report = run_report(
        calendar=mock_calendar, feed=LeakyFeed(), model=MockModelAdapter(), trading_date=MOCK_DAY
    )
    assert not any("GIFT" in f.label for f in lens(report, LensId.R02).facts)
    assert any("not licensed" in g.reason for g in lens(report, LensId.R02).gaps)


def test_proxies_name_instrument_and_method(build_report):
    report = build_report()
    proxies = [f for r in report.lenses for f in r.facts if f.data_class is DataClass.PROXY]
    assert proxies
    for f in proxies:
        assert "proxy" in f.note.lower()


def test_scenario_levels_always_name_their_instrument(build_report):
    """RC05: a futures-proxy level must never read as a cash-index level."""
    for s in build_report().scenarios:
        for text in (s.trigger, s.invalidation):
            for m in re.finditer(r"\b\d{4,}(\.\d+)?\b", text):
                assert text[m.end() :].startswith(" on "), f"unlabelled level in: {text}"


# ---- timing / honesty -----------------------------------------------------------------


def test_morning_has_no_auction_values_and_says_why(build_report):
    r07 = lens(build_report("auction_mock"), LensId.R07)
    assert not any(f.data_class is DataClass.INDICATIVE_AUCTION for f in r07.facts)
    assert any(g.data_class is DataClass.NOT_APPLICABLE and "09:00" in g.reason for g in r07.gaps)


def test_auction_addendum_values_are_labelled_indicative(build_report):
    report = build_report("auction_mock", ReportKind.AUCTION)
    assert report.id.endswith("AUCTION")
    iep = [
        f for f in lens(report, LensId.R07).facts if f.data_class is DataClass.INDICATIVE_AUCTION
    ]
    assert len(iep) == 4


def test_feed_never_serves_another_days_files():
    assert feed("full_mock").fetch(DatasetKind.NEWS, MOCK_DAY.replace(day=2)) is None


def test_holiday_and_uncovered_dates_produce_no_report(build_report):
    holiday = build_report(day=MOCK_DAY.replace(day=2))
    uncovered = build_report(day=MOCK_DAY.replace(month=11))
    assert isinstance(holiday, NoReport) and "not a trading day" in holiday.reason
    assert isinstance(uncovered, NoReport) and "does not cover" in uncovered.reason


# ---- labelling / integrity ------------------------------------------------------------


def test_mock_report_is_visibly_labelled(build_report):
    report = build_report()
    assert report.is_mock and report.id.startswith("MOCK-")
    assert render_text(report).splitlines()[0].startswith("MOCK")


def test_reviewer_rejects_mock_data_in_a_non_mock_report(build_report):
    report = build_report()
    verdict = review(list(report.lenses), report.scenarios, report.cutoff, is_mock_report=False)
    assert verdict.status is ReviewStatus.REJECTED


def test_degraded_report_is_flagged_and_gaps_are_first(build_report):
    report = build_report("no_orderflow_rights")
    assert report.review_status is ReviewStatus.DEGRADED
    text = render_text(report)
    assert text.index("DATA GAPS") < text.index("R01 Overnight news")


def test_hash_is_deterministic_and_content_sensitive(build_report):
    a, b = build_report(), build_report()
    assert a.content_hash == b.content_hash == a.compute_hash()
    assert build_report("stale_quote").content_hash != a.content_hash


def test_records_timestamped_after_cutoff_are_skipped_not_fatal(build_report, mock_calendar):
    """A late quote / catalyst is dropped from this report; the report still builds."""
    from desk.agents.model import MockModelAdapter
    from desk.pipeline import run_report

    base = feed("full_mock")
    late = "2026-10-01T08:50:00+05:30"  # 5 minutes after the 08:45 IST cutoff

    class LateFeed:
        name, is_mock = "late", True

        def capabilities(self):
            return base.capabilities()

        def fetch(self, kind, day):
            ds = base.fetch(kind, day)
            if kind is DatasetKind.GLOBAL:
                ds.records[2] = ds.records[2] | {"as_of": late}
            if kind is DatasetKind.STOCKS:
                ds.records[0] = ds.records[0] | {
                    "catalysts": [c | {"published_at": late} for c in ds.records[0]["catalysts"]]
                }
            return ds

    report = run_report(
        calendar=mock_calendar, feed=LateFeed(), model=MockModelAdapter(), trading_date=MOCK_DAY
    )
    assert not any("Nikkei" in f.label for f in lens(report, LensId.R02).facts)
    assert "MOCKBANK_A" not in {f.instrument for f in lens(report, LensId.R05).facts}
    assert all(f.as_of <= report.cutoff for r in report.lenses for f in r.facts)
