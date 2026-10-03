"""G03 step 2: NSE public-data adapter, tested only against hand-made shape samples.

The adapter is DISABLED while G03 is BLOCKED: startup refuses it, it has no network code,
and the only source is FileSource. These tests build it directly with a file source."""

import ast
import json
import shutil
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from desk.agents.model import MockModelAdapter
from desk.config import GateBlockedError, load_settings
from desk.core.facts import DataClass, Fact
from desk.core.lens import LensId, LensStatus
from desk.feeds.base import TERMS_GAP_TOPIC, DatasetKind
from desk.feeds.nse_public import FILES, FileSource, NsePublicFeed
from desk.lenses.context import ReportKind
from desk.pipeline import run_report
from desk.report.text import render_text

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "fixtures" / "nse_public"
DAY = date(2026, 10, 1)
SAMPLE_NAME = "NSE public (sample shape)"


def nse_feed(root: Path = SAMPLES) -> NsePublicFeed:
    return NsePublicFeed(FileSource(root))


@pytest.fixture
def tmp_samples(tmp_path) -> Path:
    shutil.copytree(SAMPLES, tmp_path / "nse_public")
    return tmp_path / "nse_public"


def edit(root: Path, kind: DatasetKind, change) -> None:
    path = root / DAY.isoformat() / FILES[kind][0]
    raw = json.loads(path.read_text())
    change(raw)
    path.write_text(json.dumps(raw))


def report(mock_calendar, feed, kind=ReportKind.MORNING):
    return run_report(
        calendar=mock_calendar, feed=feed, model=MockModelAdapter(), trading_date=DAY, kind=kind
    )


def lens(rep, lid):
    return next(r for r in rep.lenses if r.lens is lid)


# ---- the sample files are labelled and small ---------------------------------------------


def test_samples_are_labelled_hand_made_and_small():
    readme = (SAMPLES / "README.md").read_text()
    assert "SHAPE SAMPLES, hand-made, not downloaded from NSE, values fictional" in readme
    assert "re-verify field names against real files on the owner's PC" in readme
    for path in (SAMPLES / DAY.isoformat()).iterdir():
        assert path.stat().st_size < 16_000, path


# ---- parsing each sample -----------------------------------------------------------------


def test_pre_open_sample_parses():
    ds = nse_feed().fetch(DatasetKind.PRE_OPEN, DAY)
    assert ds.as_of == datetime(2026, 10, 1, 3, 37, 58, tzinfo=UTC)  # 09:07:58 IST
    assert ds.records[0] == {
        "instrument": "SAMPLE_BANK",
        "iep": Decimal("1658.00"),
        "imbalance_qty": Decimal("42000"),  # 95000 buy - 53000 sell
        "unit": "INR",
    }
    assert ds.records[1]["imbalance_qty"] == Decimal("-18000")


def test_option_chain_sample_parses_nearest_expiry_only():
    ds = nse_feed().fetch(DatasetKind.OPTION_CHAIN, DAY)
    assert ds.as_of == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)  # 15:30 IST prior session
    assert ds.meta["expiry"] == "2026-10-06"  # list order in file is not trusted
    assert ds.meta["spot"] == Decimal("25120.35")
    assert [r["strike"] for r in ds.records] == [Decimal(s) for s in range(24900, 25301, 100)]
    assert ds.meta["strikes_skipped_one_side_missing"] == 1  # 25400 has no PE: no zero made up
    row = ds.records[0]
    assert (row["ce_oi"], row["pe_oi"], row["ce_iv"]) == (12000, 41000, Decimal("12.1"))
    assert ds.records[-1]["pe_iv"] is None  # NSE shows IV 0 when none computed
    for r in ds.records:
        for v in r.values():
            assert v is None or isinstance(v, Decimal)


def test_fii_dii_sample_parses():
    ds = nse_feed().fetch(DatasetKind.FLOWS, DAY)
    assert ds.as_of == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    got = {r["label"]: r["value"] for r in ds.records}
    assert got == {"DII net cash": Decimal("1630.10"), "FII/FPI net cash": Decimal("-1250.40")}
    assert all(r["unit"] == "INR crore" and r["provisional"] for r in ds.records)


def test_every_served_dataset_is_labelled_sample_shape_and_mock():
    feed = nse_feed()
    assert feed.is_mock
    for kind in FILES:
        ds = feed.fetch(kind, DAY)
        assert ds.source.name == SAMPLE_NAME and ds.source.is_mock
        assert ds.source.url.startswith("https://www.nseindia.com/")


# ---- rights are declared honestly --------------------------------------------------------


def test_capabilities_grant_only_what_nse_public_data_can_serve():
    feed, caps = nse_feed(), nse_feed().capabilities()
    for kind in DatasetKind:
        cap = caps.get(kind)
        if kind in FILES:
            assert cap.granted and "AI-use" in cap.terms_unconfirmed
        else:
            assert not cap.granted and cap.rights_note
            assert feed.fetch(kind, DAY) is None
    assert "order-flow" in caps.get(DatasetKind.TRADES).rights_note
    assert caps.get(DatasetKind.DEPTH).tier == ""


# ---- mapping to Facts --------------------------------------------------------------------


def test_flows_and_options_map_to_sourced_facts(mock_calendar):
    rep = report(mock_calendar, nse_feed())
    r10 = lens(rep, LensId.R10)
    fii = next(f for f in r10.facts if f.label.startswith("FII/FPI"))
    assert (fii.value, fii.unit, fii.data_class) == (
        Decimal("-1250.40"),
        "INR crore",
        DataClass.PRIOR_SESSION,
    )
    assert fii.instrument == "NSE+BSE+MSEI cash market" and "(provisional)" in fii.label
    assert fii.source.name == SAMPLE_NAME
    assert fii.as_of == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    r13 = lens(rep, LensId.R13)
    by = {f.label: f for f in r13.facts}
    assert by["ATM strike (vs prior spot close)"].value == Decimal("25100")
    assert by["Highest call OI strike"].value == Decimal("25200")
    assert by["Highest put OI strike"].value == Decimal("25000")
    assert by["ATM CE implied volatility"].unit == "% annualised"
    assert all(f.source.name == SAMPLE_NAME for f in r13.facts)
    # NSE chain has no rate / dividend inputs -> Greeks are a gap, not a number
    assert not any("delta" in f.label for f in r13.facts)
    assert any(g.topic == "Greeks" for g in r13.gaps)


def test_auction_addendum_uses_pre_open_as_indicative(mock_calendar):
    r07 = lens(report(mock_calendar, nse_feed(), ReportKind.AUCTION), LensId.R07)
    iep = next(f for f in r07.facts if f.label == "SAMPLE_BANK indicative equilibrium price")
    assert (iep.value, iep.unit, iep.data_class) == (
        Decimal("1658.00"),
        "INR",
        DataClass.INDICATIVE_AUCTION,
    )
    assert iep.as_of == datetime(2026, 10, 1, 3, 37, 58, tzinfo=UTC)
    assert any(g.topic == "depth" for g in r07.gaps)  # depth not granted


# ---- missing / malformed / stale -> Gap, never a crash or a number -----------------------


def _r_gap_reasons(rep, lid):
    return " | ".join(g.reason for g in lens(rep, lid).gaps)


def test_missing_file_is_a_gap_with_reason(mock_calendar, tmp_samples):
    (tmp_samples / DAY.isoformat() / FILES[DatasetKind.FLOWS][0]).unlink()
    rep = report(mock_calendar, nse_feed(tmp_samples))
    r10 = lens(rep, LensId.R10)
    assert r10.status is LensStatus.UNAVAILABLE and not r10.facts
    assert "not saved for 2026-10-01" in _r_gap_reasons(rep, LensId.R10)


@pytest.mark.parametrize(
    ("kind", "lid", "damage", "expect"),
    [
        (DatasetKind.OPTION_CHAIN, LensId.R13, b"{not json", "JSONDecodeError"),
        (DatasetKind.FLOWS, LensId.R10, b"\xff\xfe", "Error"),
        (DatasetKind.FLOWS, LensId.R10, b"{}", "expected a non-empty list"),
        (DatasetKind.OPTION_CHAIN, LensId.R13, b'{"records": {}}', "KeyError"),
    ],
)
def test_malformed_file_is_a_gap_not_a_crash(mock_calendar, tmp_samples, kind, lid, damage, expect):
    (tmp_samples / DAY.isoformat() / FILES[kind][0]).write_bytes(damage)
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert lens(rep, lid).status is LensStatus.UNAVAILABLE
    reasons = _r_gap_reasons(rep, lid)
    assert "could not read" in reasons and expect in reasons


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw[0].update(netValue="9999.99"),  # net != buy - sell
        lambda raw: raw[0].update(category="Retail"),
        lambda raw: raw[0].update(buyValue="abc"),
        lambda raw: raw[1].update(date="29-Sep-2026"),  # rows disagree on the day
    ],
)
def test_inconsistent_fii_dii_rows_are_refused(mock_calendar, tmp_samples, change):
    edit(tmp_samples, DatasetKind.FLOWS, change)
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert not lens(rep, LensId.R10).facts
    assert "could not read" in _r_gap_reasons(rep, LensId.R10)


def test_option_chain_bad_number_is_a_gap(mock_calendar, tmp_samples):
    edit(
        tmp_samples,
        DatasetKind.OPTION_CHAIN,
        lambda raw: raw["records"]["data"][0]["CE"].update(openInterest=None),
    )
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert not lens(rep, LensId.R13).facts
    assert "not a number" in _r_gap_reasons(rep, LensId.R13)


def test_stale_option_chain_and_flows_are_marked_stale(mock_calendar, tmp_samples):
    edit(
        tmp_samples,
        DatasetKind.OPTION_CHAIN,
        lambda raw: raw["records"].update(timestamp="29-Sep-2026 15:30:00"),
    )
    edit(
        tmp_samples,
        DatasetKind.FLOWS,
        lambda raw: [r.update(date="29-Sep-2026") for r in raw],
    )
    rep = report(mock_calendar, nse_feed(tmp_samples))
    for lid in (LensId.R10, LensId.R13):
        r = lens(rep, lid)
        assert all(f.data_class is not DataClass.PRIOR_SESSION for f in r.facts)
        assert all(f.data_class is not DataClass.DERIVED for f in r.facts)
        assert any(
            g.data_class is DataClass.STALE and "expected prior session 2026-09-30" in g.reason
            for g in r.gaps
        )
    assert not any("delta" in f.label for f in lens(rep, LensId.R13).facts)


def test_yesterdays_pre_open_is_never_shown_as_indicative(mock_calendar, tmp_samples):
    def back_a_day(raw):
        for row in raw["data"]:
            row["detail"]["preOpenMarket"]["lastUpdateTime"] = "30-Sep-2026 09:07:58"

    edit(tmp_samples, DatasetKind.PRE_OPEN, back_a_day)
    r07 = lens(report(mock_calendar, nse_feed(tmp_samples), ReportKind.AUCTION), LensId.R07)
    assert not any(f.data_class is DataClass.INDICATIVE_AUCTION for f in r07.facts)
    assert any(g.topic == "pre_open" and g.data_class is DataClass.STALE for g in r07.gaps)


def test_one_old_pre_open_row_makes_the_dataset_old(tmp_samples):
    def one_old(raw):
        raw["data"][1]["detail"]["preOpenMarket"]["lastUpdateTime"] = "30-Sep-2026 09:07:58"

    edit(tmp_samples, DatasetKind.PRE_OPEN, one_old)
    ds = nse_feed(tmp_samples).fetch(DatasetKind.PRE_OPEN, DAY)
    assert ds.as_of.date() == date(2026, 9, 30)


def test_another_days_folder_is_never_served():
    feed = nse_feed()
    assert feed.fetch(DatasetKind.FLOWS, date(2026, 10, 2)) is None
    assert "not saved for 2026-10-02" in feed.problems()[DatasetKind.FLOWS]


# ---- disabled while G03 is blocked -------------------------------------------------------


def test_startup_refuses_nse_public_while_g03_blocked():
    with pytest.raises(GateBlockedError, match=r"nse_public.*DISABLED.*G03"):
        load_settings({"DESK_FEED_ADAPTER": "nse_public"})
    with pytest.raises(GateBlockedError, match="G03"):
        load_settings({"DESK_FEED_ADAPTER": "kite"})


NETWORK_MODULES = {"httpx", "urllib", "urllib3", "socket", "requests", "http", "aiohttp", "ssl"}


def _desk_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_adapter_and_everything_it_imports_has_no_network_code():
    src = REPO / "src"
    todo, seen = ["desk.feeds.nse_public"], set()
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        base = src.joinpath(*mod.split("."))
        path = base.with_suffix(".py") if base.with_suffix(".py").exists() else base / "__init__.py"
        for name in _desk_imports(path):
            assert name.split(".")[0] not in NETWORK_MODULES, f"{mod} imports {name}"
            if name.split(".")[0] == "desk":
                todo.append(name)
    assert {"desk.feeds.nse_public", "desk.feeds.base", "desk.core.facts"} <= seen
    text = (src / "desk" / "feeds" / "nse_public.py").read_text()
    for call in ("urlopen", "create_connection", "httpx.", "requests."):
        assert call not in text


# ---- end to end --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(ReportKind))
def test_end_to_end_report_has_all_15_lenses_and_shows_gaps(mock_calendar, kind):
    rep = report(mock_calendar, nse_feed(), kind)
    assert [r.lens for r in rep.lenses] == list(LensId)
    assert rep.is_mock and rep.id.startswith("MOCK-") and rep.feed.startswith("nse_public:")
    for r in rep.lenses:
        for f in r.facts:
            assert isinstance(f, Fact) and f.source and f.as_of and f.instrument and f.unit
            assert not isinstance(f.value, float)
        if r.status is LensStatus.UNAVAILABLE:
            assert r.gaps
    for lid in (LensId.R01, LensId.R02, LensId.R03, LensId.R08):
        assert lens(rep, lid).status is LensStatus.UNAVAILABLE
    assert lens(rep, LensId.R10).facts and lens(rep, LensId.R13).facts
    # no value from a dataset the feed has no rights to
    assert not any(f.source.name.startswith("MOCK feed") for r in rep.lenses for f in r.facts)
    terms = [g for g in lens(rep, LensId.R15).gaps if g.topic == TERMS_GAP_TOPIC]
    assert len(terms) == 1 and "AI-use" in terms[0].reason
    assert ("pre_open" in terms[0].reason) is (kind is ReportKind.AUCTION)
    assert any("AI-use" in line for line in rep.top_gaps)
    text = render_text(rep)
    assert "Data usage terms" in text and "R15" in text and "Trade data" in text


def test_fixture_feed_reports_have_no_terms_gap(build_report):
    rep = build_report()
    assert not any(g.topic == TERMS_GAP_TOPIC for r in rep.lenses for g in r.gaps)


# ---- step 2b: sectors (all indices), stocks (CM bhavcopy), participant OI ---------------

NEW_2B = (DatasetKind.SECTORS, DatasetKind.STOCKS, DatasetKind.FNO)


def edit_text(root: Path, kind: DatasetKind, change) -> None:
    path = root / DAY.isoformat() / FILES[kind][0]
    path.write_text(change(path.read_text()))


def _all_decimal(obj) -> bool:
    if isinstance(obj, dict):
        return all(_all_decimal(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_all_decimal(v) for v in obj)
    return not isinstance(obj, float)


def test_2b_samples_exist_and_are_in_readme():
    readme = (SAMPLES / "README.md").read_text()
    for kind in NEW_2B:
        name = FILES[kind][0]
        assert (SAMPLES / DAY.isoformat() / name).exists()
        assert f"`{name}`" in readme and f"`{kind.value}`" in readme


def test_all_indices_sample_parses():
    ds = nse_feed().fetch(DatasetKind.SECTORS, DAY)
    assert ds.as_of == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert ds.meta["index_return_pct"] == Decimal("0.48")
    assert ds.meta["rows_ignored"] == 1  # NIFTY NEXT 50 is not a sector index
    by = {r["sector"]: r for r in ds.records}
    assert set(by) == {"AUTO", "BANK", "ENERGY", "IT"}
    assert by["BANK"] == {
        "sector": "BANK",
        "return_pct": Decimal("0.82"),
        "advances": Decimal("9"),
        "declines": Decimal("3"),
        "weight_pct": None,  # not in the file: never invented
    }
    assert _all_decimal(ds.records) and _all_decimal(ds.meta)


def test_cm_bhavcopy_sample_reads_only_the_universe_eq_rows():
    ds = nse_feed().fetch(DatasetKind.STOCKS, DAY)
    assert ds.as_of == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert [r["symbol"] for r in ds.records] == ["SAMPLE_BANK", "SAMPLE_OIL"]
    oil = ds.records[1]
    # the EQ row, not the BE row of the same symbol
    assert (oil["prev_open"], oil["prev_high"], oil["prev_low"], oil["prev_close"]) == (
        Decimal("1428.00"),
        Decimal("1431.00"),
        Decimal("1405.00"),
        Decimal("1420.00"),
    )
    assert ds.meta["catalysts_sourced"] is False and ds.meta["symbols_missing"] == []
    assert all(r["catalysts"] == [] and r["weight_pct"] is None for r in ds.records)
    assert _all_decimal(ds.records)


def test_bhavcopy_universe_is_not_widened(tmp_samples):
    edit_text(tmp_samples, DatasetKind.STOCKS, lambda t: t.replace("SAMPLE_OIL,EQ", "SAMPLE_X,EQ"))
    ds = nse_feed(tmp_samples).fetch(DatasetKind.STOCKS, DAY)
    assert [r["symbol"] for r in ds.records] == ["SAMPLE_BANK"]
    assert ds.meta["symbols_missing"] == ["SAMPLE_OIL"]
    with pytest.raises(ValueError, match="universe"):
        NsePublicFeed(FileSource(SAMPLES), stock_universe=())


def test_participant_oi_sample_parses():
    ds = nse_feed().fetch(DatasetKind.FNO, DAY)
    assert ds.as_of == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert ds.records == []  # no contract-level OI in this file
    net = {p["participant"]: p["net_contracts"] for p in ds.meta["participant_oi"]}
    assert net == {
        "Client": Decimal("115000"),
        "DII": Decimal("-25000"),
        "FII": Decimal("-120000"),  # 80000 long - 200000 short
        "Pro": Decimal("30000"),
    }
    assert sum(net.values()) == 0
    assert "ban_list" not in ds.meta and "spot_close" not in ds.meta
    assert _all_decimal(ds.meta)


def test_2b_datasets_are_granted_sample_labelled_and_terms_unconfirmed(mock_calendar):
    feed = nse_feed()
    caps = feed.capabilities()
    for kind in NEW_2B:
        assert caps.get(kind).granted and "AI-use" in caps.get(kind).terms_unconfirmed
        ds = feed.fetch(kind, DAY)
        assert ds.source.name == SAMPLE_NAME and ds.source.is_mock
    rep = report(mock_calendar, feed)
    terms = next(g for g in lens(rep, LensId.R15).gaps if g.topic == TERMS_GAP_TOPIC)
    for name in ("sectors", "stocks", "fno", "flows", "option_chain"):
        assert name in terms.reason


def test_sectors_map_to_facts_with_a_weights_gap(mock_calendar):
    r04 = lens(report(mock_calendar, nse_feed()), LensId.R04)
    by = {f.label: f for f in r04.facts}
    bank = by["BANK return"]
    assert (bank.value, bank.unit, bank.data_class) == (
        Decimal("0.82"),
        "%",
        DataClass.PRIOR_SESSION,
    )
    assert bank.instrument == "NIFTY BANK (sector index)" and bank.source.name == SAMPLE_NAME
    assert by["BANK vs Nifty 50"].value == Decimal("0.34")
    assert by["BANK vs Nifty 50"].data_class is DataClass.DERIVED
    assert by["IT breadth (adv/dec)"].value == "3/7"
    assert not any("weight" in f.label for f in r04.facts)
    assert any(g.topic == "sector weights" for g in r04.gaps)
    assert r04.status is not LensStatus.UNAVAILABLE


def test_stocks_map_to_levels_and_never_claim_zero_catalysts(mock_calendar):
    rep = report(mock_calendar, nse_feed())
    r05 = lens(rep, LensId.R05)
    by = {f.label: f for f in r05.facts}
    assert set(by) == {
        f"{s} prior session {p}"
        for s in ("SAMPLE_BANK", "SAMPLE_OIL")
        for p in ("close", "high", "low")
    }
    close = by["SAMPLE_BANK prior session close"]
    assert (close.value, close.unit, close.instrument, close.data_class) == (
        Decimal("1650.00"),
        "INR",
        "SAMPLE_BANK",
        DataClass.PRIOR_SESSION,
    )
    assert not any("catalyst" in f.label for f in r05.facts)  # no "0 catalysts" fact
    assert any(g.topic == "catalysts" for g in r05.gaps)
    # no shortlist, so no watch names handed to R14
    assert not any(f.label.endswith("prior close") for f in r05.facts)


def test_participant_oi_maps_to_r09_with_ban_list_and_contract_gaps(mock_calendar):
    r09 = lens(report(mock_calendar, nse_feed()), LensId.R09)
    by = {f.label: f for f in r09.facts}
    fii = by["FII net index futures OI"]
    assert (fii.value, fii.unit, fii.data_class) == (
        Decimal("-120000"),
        "contracts",
        DataClass.PRIOR_SESSION,
    )
    assert fii.source.name == SAMPLE_NAME
    assert "F&O ban list" not in by  # never "none listed" for a list the file lacks
    topics = {g.topic for g in r09.gaps}
    assert {"F&O ban list", "contract open interest"} <= topics
    assert not any("basis" in f.label or "OI change" in f.label for f in r09.facts)


def test_fixture_feed_still_shows_weights_ban_list_and_shortlist(build_report):
    rep = build_report()
    r04, r05, r09 = (lens(rep, lid) for lid in (LensId.R04, LensId.R05, LensId.R09))
    assert any("weight in Nifty 50" in f.label for f in r04.facts)
    assert not any(g.topic == "sector weights" for g in r04.gaps)
    assert any(f.label.endswith("prior close") for f in r05.facts)
    assert any(f.label == "F&O ban list" for f in r09.facts)
    assert not any(g.topic in {"F&O ban list", "contract open interest"} for g in r09.gaps)


@pytest.mark.parametrize(
    ("kind", "lid"),
    [
        (DatasetKind.SECTORS, LensId.R04),
        (DatasetKind.STOCKS, LensId.R05),
        (DatasetKind.FNO, LensId.R09),
    ],
)
def test_2b_missing_file_is_a_gap(mock_calendar, tmp_samples, kind, lid):
    (tmp_samples / DAY.isoformat() / FILES[kind][0]).unlink()
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert lens(rep, lid).status is LensStatus.UNAVAILABLE and not lens(rep, lid).facts
    assert "not saved for 2026-10-01" in _r_gap_reasons(rep, lid)


def _pct(raw, value):
    raw["data"][3]["percentChange"] = value  # NIFTY BANK


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        (lambda raw: _pct(raw, "1.82"), "percentChange"),
        (lambda raw: raw["data"].pop(0), "no NIFTY 50 row"),
        (lambda raw: raw["data"][3].update(advances="-1"), "whole count"),
        (lambda raw: raw["data"][3].update(last=None), "not a number"),
        (lambda raw: [r.update(key="BROAD MARKET INDICES") for r in raw["data"]], "SECTORAL"),
        (lambda raw: raw["data"][5].update(index="NIFTY BANK"), "repeats"),
    ],
)
def test_malformed_all_indices_is_a_gap(mock_calendar, tmp_samples, change, expect):
    edit(tmp_samples, DatasetKind.SECTORS, change)
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert not lens(rep, LensId.R04).facts
    reasons = _r_gap_reasons(rep, LensId.R04)
    assert "could not read" in reasons and expect in reasons


BANK_ROW = "SAMPLE_BANK,EQ,,,,,SAMPLE_BANK SAMPLE LTD,1640.00,1662.00,1631.50,1650.00"


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        (lambda t: t.replace("ClsPric", "Close"), "missing columns"),
        (lambda t: t.replace(BANK_ROW, BANK_ROW.replace("1662.00", "1645.00")), "inconsistent"),
        (lambda t: t.replace(BANK_ROW, BANK_ROW.replace("1650.00", "abc")), "not a number"),
        (lambda t: t + t.splitlines()[1] + "\n", "repeats"),
        (
            lambda t: t.replace("SAMPLE_BANK,EQ", "SAMPLE_BANK,BE").replace("OIL,EQ", "OIL,BL"),
            "no universe",
        ),
        (
            lambda t: t.replace(
                "2026-09-30,2026-09-30,CM,NSE,STK,90002", "2026-09-29,2026-09-29,CM,NSE,STK,90002"
            ),
            "disagree",
        ),
        (lambda t: t.splitlines()[0] + "\n", "no rows"),
        (lambda t: t.replace(",F1,1,,,,,", ",F1,1,,,,,,extra"), "more cells"),
    ],
)
def test_malformed_bhavcopy_is_a_gap(mock_calendar, tmp_samples, change, expect):
    edit_text(tmp_samples, DatasetKind.STOCKS, change)
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert not lens(rep, LensId.R05).facts
    reasons = _r_gap_reasons(rep, LensId.R05)
    assert "could not read" in reasons and expect in reasons


def _oi_cell(text: str, who: str, col: int, value: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        cells = line.split(",")
        if cells[0] == who:
            cells[col] = value
            lines[i] = ",".join(cells)
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        # FII long +1 everywhere it is counted except TOTAL -> column sum breaks
        (
            lambda t: _oi_cell(_oi_cell(t, "FII", 1, "80001"), "FII", 13, "2950001"),
            "add up to TOTAL",
        ),
        # FII and TOTAL "Total Long" +1: columns still add up, but FII's row total does not
        (
            lambda t: _oi_cell(_oi_cell(t, "FII", 13, "2950001"), "TOTAL", 13, "15851001"),
            "FII Total Long does not add up",
        ),
        (
            lambda t: "\n".join(x for x in t.splitlines() if not x.startswith("FII")) + "\n",
            "incomplete",
        ),
        (lambda t: t.replace("as on Sep 30, 2026", "for the day"), "title line"),
        (lambda t: _oi_cell(t, "Pro", 2, "x"), "not a number"),
        (lambda t: t.replace("\nDII,", "\nMF,"), "unexpected"),
        (lambda t: t.replace("Future Index Long", "Fut Idx Long"), "KeyError"),
    ],
)
def test_malformed_participant_oi_is_a_gap(mock_calendar, tmp_samples, change, expect):
    edit_text(tmp_samples, DatasetKind.FNO, change)
    rep = report(mock_calendar, nse_feed(tmp_samples))
    assert not lens(rep, LensId.R09).facts
    reasons = _r_gap_reasons(rep, LensId.R09)
    assert "could not read" in reasons and expect in reasons


def test_unbalanced_market_wide_participant_oi_is_refused(tmp_samples):
    # move 1000 index-future shorts from FII to nobody, keeping every row and TOTAL consistent
    def unbalance(t):
        t = _oi_cell(t, "FII", 2, "199000")
        t = _oi_cell(t, "FII", 14, "3299000")
        t = _oi_cell(t, "TOTAL", 2, "399000")
        return _oi_cell(t, "TOTAL", 14, "15850000")

    edit_text(tmp_samples, DatasetKind.FNO, unbalance)
    feed = nse_feed(tmp_samples)
    assert feed.fetch(DatasetKind.FNO, DAY) is None
    assert "TOTAL Future Index Long != Future Index Short" in feed.problems()[DatasetKind.FNO]


def test_stale_2b_datasets_are_marked_stale(mock_calendar, tmp_samples):
    edit(tmp_samples, DatasetKind.SECTORS, lambda raw: raw.update(timestamp="29-Sep-2026 15:30:00"))
    edit_text(tmp_samples, DatasetKind.STOCKS, lambda t: t.replace("2026-09-30", "2026-09-29"))
    edit_text(tmp_samples, DatasetKind.FNO, lambda t: t.replace("Sep 30, 2026", "Sep 29, 2026"))
    rep = report(mock_calendar, nse_feed(tmp_samples))
    for lid in (LensId.R04, LensId.R05, LensId.R09):
        r = lens(rep, lid)
        assert r.facts
        assert all(f.data_class in {DataClass.STALE} for f in r.facts), lid
        assert any(
            g.data_class is DataClass.STALE and "expected prior session 2026-09-30" in g.reason
            for g in r.gaps
        )


def test_end_to_end_2b_lenses_populated_from_samples_and_mock(mock_calendar):
    rep = report(mock_calendar, nse_feed())
    assert rep.is_mock and rep.id.startswith("MOCK-")
    for lid in (LensId.R04, LensId.R05, LensId.R09, LensId.R10, LensId.R13):
        r = lens(rep, lid)
        assert r.facts and r.status is not LensStatus.UNAVAILABLE, lid
        for f in r.facts:
            assert f.source.is_mock and f.source.name == SAMPLE_NAME
            assert not isinstance(f.value, float)
    text = render_text(rep)
    for line in ("BANK return: 0.82 %", "SAMPLE_OIL prior session close: 1420.00 INR"):
        assert line in text
    assert "FII net index futures OI: -120000 contracts" in text
    assert "none listed" not in text
