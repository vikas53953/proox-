"""NSE public-published-data adapter (G03 staged direction, steps 2 and 2b). DISABLED.

Serves six datasets from NSE's public pages: pre-open market (09:00-09:08 IST indicative
auction), index option chain, FII/DII provisional cash activity (step 2), and, from
step 2b, sectoral index closes ("all indices"), prior-session OHLC from the CM bhavcopy
for a fixed stock universe, and F&O participant-wise open interest. Every other dataset
is declared NOT granted, with the reason, so lenses show a gap and never an invented
value.

There is NO network code here. The adapter reads through an injected `source`; the only
source that exists is `FileSource`, which reads files saved on disk. While G03 is BLOCKED,
`config.load_settings` refuses DESK_FEED_ADAPTER=nse_public, so this only runs in tests.

Field names follow the public JSON shapes as best documented; the sample files are
hand-made (fixtures/nse_public/README.md) and must be re-checked against real files on
the owner's PC before G03 closes.
"""

import csv
import io
import json
import re
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from desk.core.facts import Source
from desk.feeds.base import Capabilities, Capability, Dataset, DatasetKind
from desk.market_calendar import IST

NSE_ADAPTER_VERSION = "nse-public-v0-shape-samples"
TERMS_UNCONFIRMED = "NSE usage / redistribution / AI-use terms not confirmed by owner (G03)"
SESSION_CLOSE_IST = time(15, 30)

# dataset -> (file name inside <root>/<YYYY-MM-DD>/, source id, public page)
FILES: dict[DatasetKind, tuple[str, str, str]] = {
    DatasetKind.PRE_OPEN: (
        "pre_open_nifty.json",
        "nse-public-pre-open",
        "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market",
    ),
    DatasetKind.OPTION_CHAIN: (
        "option_chain_nifty.json",
        "nse-public-option-chain",
        "https://www.nseindia.com/option-chain",
    ),
    DatasetKind.FLOWS: (
        "fii_dii_provisional.json",
        "nse-public-fii-dii",
        "https://www.nseindia.com/reports/fii-dii",
    ),
    DatasetKind.SECTORS: (
        "all_indices.json",
        "nse-public-all-indices",
        "https://www.nseindia.com/market-data/index-performances",
    ),
    DatasetKind.STOCKS: (
        "cm_bhavcopy.csv",
        "nse-public-cm-bhavcopy",
        "https://www.nseindia.com/all-reports",
    ),
    DatasetKind.FNO: (
        "fao_participant_oi.csv",
        "nse-public-participant-oi",
        "https://www.nseindia.com/all-reports-derivatives",
    ),
}

# Stocks the adapter reads from the bhavcopy. The sample universe matches the symbols
# the pre-open sample uses; the real Nifty 50 list arrives with G03, not before.
SAMPLE_STOCK_UNIVERSE = ("SAMPLE_BANK", "SAMPLE_OIL")

NOT_SERVED: dict[DatasetKind, str] = {
    DatasetKind.NEWS: "news is not NSE published market data; not served by NSE public adapter",
    DatasetKind.GLOBAL: "global markets are not NSE data; not served by NSE public adapter",
    DatasetKind.GIFT_NIFTY: "GIFT Nifty is an NSE IX product; not in NSE public adapter",
    DatasetKind.MACRO: "macro / commodities / FX are not served by NSE public adapter",
    DatasetKind.INDEX_BARS: "intraday index bars are not part of NSE public published data",
    DatasetKind.FUT_BARS: "intraday futures bars are not part of NSE public published data",
    DatasetKind.DEPTH: "no licensed depth / spread statistics in NSE public data",
    DatasetKind.TRADES: "no trade-event / order-flow rights in NSE public data",
}


class NseDataError(ValueError):
    """A saved file is missing, unreadable or not in the expected shape."""


class NseSource(Protocol):
    """Where the adapter gets bytes from. Only FileSource exists (no network while G03)."""

    name: str
    is_sample: bool

    def read(self, trading_date: date, file_name: str) -> bytes | None: ...


class FileSource:
    """Reads files saved under <root>/<YYYY-MM-DD>/. `sample=True` (the default) labels
    every value as a hand-made sample shape: the feed and its sources are then MOCK."""

    def __init__(self, root: Path, *, sample: bool = True) -> None:
        self.root = Path(root)
        self.name = f"file:{self.root.name}"
        self.is_sample = sample

    def read(self, trading_date: date, file_name: str) -> bytes | None:
        path = self.root / trading_date.isoformat() / file_name
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None


def _ist(text: str, fmt: str) -> datetime:
    return datetime.strptime(text, fmt).replace(tzinfo=IST)


def _session_close(session: date) -> datetime:
    return datetime.combine(session, SESSION_CLOSE_IST, tzinfo=IST)


def _num(v: Any, what: str) -> Decimal:
    if isinstance(v, bool) or v is None:
        raise NseDataError(f"{what}: not a number ({v!r})")
    if isinstance(v, float):  # parse_float=Decimal means this never comes from a file
        raise NseDataError(f"{what}: float value")
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except InvalidOperation as exc:
        raise NseDataError(f"{what}: not a number ({v!r})") from exc
    if not d.is_finite():
        raise NseDataError(f"{what}: not a finite number")
    return d


def _iv(side: dict, what: str) -> Decimal | None:
    v = _num(side["impliedVolatility"], what)
    return v if v > 0 else None  # NSE shows 0 when no IV was computed: no value, not 0%


def parse_pre_open(raw: Any) -> tuple[datetime, list[dict], dict]:
    """market-data-pre-open?key=NIFTY: data[].metadata.symbol + detail.preOpenMarket."""
    rows = raw["data"]
    if not isinstance(rows, list) or not rows:
        raise NseDataError("pre-open: no rows")
    records, times = [], []
    for row in rows:
        symbol = str(row["metadata"]["symbol"]).strip()
        pom = row["detail"]["preOpenMarket"]
        if not symbol:
            raise NseDataError("pre-open: empty symbol")
        buy = _num(pom["totalBuyQuantity"], f"{symbol} totalBuyQuantity")
        sell = _num(pom["totalSellQuantity"], f"{symbol} totalSellQuantity")
        records.append(
            {
                "instrument": symbol,
                "iep": _num(pom["IEP"], f"{symbol} IEP"),
                "imbalance_qty": buy - sell,
                "unit": "INR",
            }
        )
        times.append(_ist(pom["lastUpdateTime"], "%d-%b-%Y %H:%M:%S"))
    # the OLDEST row time is the dataset time, so one old row cannot hide behind new ones
    return min(times), records, {"rows": len(records)}


def parse_option_chain(raw: Any) -> tuple[datetime, list[dict], dict]:
    """option-chain-indices?symbol=NIFTY: records.{timestamp, underlyingValue,
    expiryDates, data[strikePrice, expiryDate, CE{...}, PE{...}]}. Nearest expiry only."""
    rec = raw["records"]
    as_of = _ist(rec["timestamp"], "%d-%b-%Y %H:%M:%S")
    expiries = sorted(_ist(e, "%d-%b-%Y").date() for e in rec["expiryDates"])
    if not expiries:
        raise NseDataError("option chain: no expiry dates")
    # an expiry is live until SESSION_CLOSE_IST on its day; NSE can still list it after
    live = [
        e
        for e in expiries
        if e > as_of.date() or (e == as_of.date() and as_of.time() < SESSION_CLOSE_IST)
    ]
    if not live:
        raise NseDataError("option chain: every listed expiry has passed")
    near = live[0]
    near_text = near.strftime("%d-%b-%Y")
    records, skipped, underlying = [], 0, ""
    for row in rec["data"]:
        if row["expiryDate"] != near_text:
            continue
        ce, pe = row.get("CE"), row.get("PE")
        if not ce or not pe:
            skipped += 1  # one side not listed: no zero is invented for it
            continue
        underlying = underlying or str(ce.get("underlying") or pe.get("underlying") or "")
        strike = _num(row["strikePrice"], "strikePrice")
        records.append(
            {
                "strike": strike,
                "ce_oi": _num(ce["openInterest"], f"{strike} CE openInterest"),
                "pe_oi": _num(pe["openInterest"], f"{strike} PE openInterest"),
                "ce_vol": _num(ce["totalTradedVolume"], f"{strike} CE volume"),
                "pe_vol": _num(pe["totalTradedVolume"], f"{strike} PE volume"),
                "ce_oi_chg": _num(ce["changeinOpenInterest"], f"{strike} CE OI change"),
                "pe_oi_chg": _num(pe["changeinOpenInterest"], f"{strike} PE OI change"),
                "ce_iv": _iv(ce, f"{strike} CE IV"),
                "pe_iv": _iv(pe, f"{strike} PE IV"),
            }
        )
    if not records:
        raise NseDataError(f"option chain: no strikes with both sides for {near_text}")
    meta = {
        "underlying": f"{underlying or 'index'} (NSE index options)",
        "expiry": near.isoformat(),
        "spot": _num(rec["underlyingValue"], "underlyingValue"),
        "oi_unit": "contracts",
        "strikes_skipped_one_side_missing": skipped,
        # no risk-free rate / dividend yield in this file -> R13 shows a Greeks gap
    }
    return as_of, records, meta


FII_LABELS = {"FII/FPI": "FII/FPI net cash", "DII": "DII net cash"}


def parse_fii_dii(raw: Any) -> tuple[datetime, list[dict], dict]:
    """fiidiiTradeReact: [{category, date, buyValue, sellValue, netValue}] in INR crore,
    provisional, NSE+BSE+MSEI combined. The file has no publish time: as of = 15:30 IST
    of the session the figures describe."""
    if not isinstance(raw, list) or not raw:
        raise NseDataError("FII/DII: expected a non-empty list")
    records, dates = [], set()
    for row in raw:
        cat = str(row["category"]).replace("*", "").strip()
        label = FII_LABELS.get(cat)
        if label is None:
            raise NseDataError(f"FII/DII: unknown category {row['category']!r}")
        buy = _num(row["buyValue"], f"{cat} buyValue")
        sell = _num(row["sellValue"], f"{cat} sellValue")
        net = _num(row["netValue"], f"{cat} netValue")
        if buy - sell != net:
            raise NseDataError(f"FII/DII: {cat} net {net} != buy {buy} - sell {sell}")
        dates.add(_ist(row["date"], "%d-%b-%Y").date())
        records.append(
            {
                "label": label,
                "instrument": "NSE+BSE+MSEI cash market",
                "value": net,
                "unit": "INR crore",
                "provisional": True,
            }
        )
    if len(dates) != 1 or len(records) != len({r["label"] for r in records}):
        raise NseDataError("FII/DII: rows disagree on date or repeat a category")
    session = dates.pop()
    return (
        _session_close(session),
        records,
        {"margin_financing_sourced": False, "session": session.isoformat()},
    )


def _count(v: Any, what: str) -> Decimal:
    d = _num(v, what)
    if d < 0 or d != d.to_integral_value():
        raise NseDataError(f"{what}: not a whole count ({v!r})")
    return d


PCT_TOLERANCE = Decimal("0.01")


def parse_all_indices(raw: Any) -> tuple[datetime, list[dict], dict]:
    """allIndices: {timestamp, data[{key, index, last, previousClose, percentChange,
    advances, declines}]}. NIFTY 50 gives the index return; rows keyed
    "SECTORAL INDICES" are the sectors. The file has no Nifty 50 weights."""
    as_of = _ist(raw["timestamp"], "%d-%b-%Y %H:%M:%S")
    rows = raw["data"]
    if not isinstance(rows, list) or not rows:
        raise NseDataError("all indices: no rows")
    index_ret, records, ignored = None, [], 0
    for row in rows:
        name = str(row["index"]).strip()
        last = _num(row["last"], f"{name} last")
        prev = _num(row["previousClose"], f"{name} previousClose")
        pct = _num(row["percentChange"], f"{name} percentChange")
        if last <= 0 or prev <= 0:
            raise NseDataError(f"all indices: {name} non-positive level")
        computed = ((last - prev) / prev * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if abs(computed - pct) > PCT_TOLERANCE:
            raise NseDataError(f"all indices: {name} percentChange {pct} != {computed}")
        if name == "NIFTY 50":
            index_ret = pct
        elif str(row.get("key", "")).strip() == "SECTORAL INDICES":
            records.append(
                {
                    "sector": name.removeprefix("NIFTY ").strip(),
                    "return_pct": pct,
                    "advances": _count(row["advances"], f"{name} advances"),
                    "declines": _count(row["declines"], f"{name} declines"),
                    "weight_pct": None,  # not in this file: R04 shows a gap, not a weight
                }
            )
        else:
            ignored += 1
    if index_ret is None:
        raise NseDataError("all indices: no NIFTY 50 row")
    if not records:
        raise NseDataError("all indices: no SECTORAL INDICES rows")
    if len({r["sector"] for r in records}) != len(records):
        raise NseDataError("all indices: a sector index repeats")
    meta = {"index_return_pct": index_ret, "weights_sourced": False, "rows_ignored": ignored}
    return as_of, records, meta


def _csv_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise NseDataError("csv: no header")
    reader.fieldnames = [str(f).strip() for f in reader.fieldnames]
    rows = []
    for row in reader:
        if None in row:
            raise NseDataError("csv: row has more cells than the header")
        rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


BHAV_COLUMNS = ("TradDt", "TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric", "ClsPric")


def parse_cm_bhavcopy(text: str, universe: tuple[str, ...]) -> tuple[datetime, list[dict], dict]:
    """CM bhavcopy (UDiFF CSV): TradDt, TckrSymb, SctySrs, OpnPric, HghPric, LwPric,
    ClsPric, ... Only series EQ rows of the given universe are read; other symbols are
    ignored (the universe is never widened). No catalysts and no index weights here."""
    rows = _csv_rows(text)
    if not rows:
        raise NseDataError("bhavcopy: no rows")
    missing_cols = [c for c in BHAV_COLUMNS if c not in rows[0]]
    if missing_cols:
        raise NseDataError(f"bhavcopy: missing columns {missing_cols}")
    wanted, found, dates = set(universe), {}, set()
    for row in rows:
        sym = row["TckrSymb"]
        if sym not in wanted or row["SctySrs"] != "EQ":
            continue
        if sym in found:
            raise NseDataError(f"bhavcopy: {sym} EQ row repeats")
        o, h = _num(row["OpnPric"], f"{sym} OpnPric"), _num(row["HghPric"], f"{sym} HghPric")
        lo, c = _num(row["LwPric"], f"{sym} LwPric"), _num(row["ClsPric"], f"{sym} ClsPric")
        if not (0 < lo <= min(o, c) and max(o, c) <= h):
            raise NseDataError(f"bhavcopy: {sym} OHLC inconsistent ({o}/{h}/{lo}/{c})")
        dates.add(date.fromisoformat(row["TradDt"]))
        found[sym] = {
            "symbol": sym,
            "prev_open": o,
            "prev_high": h,
            "prev_low": lo,
            "prev_close": c,
            "weight_pct": None,
            "catalysts": [],
        }
    if not found:
        raise NseDataError("bhavcopy: no universe symbol in series EQ")
    if len(dates) != 1:
        raise NseDataError("bhavcopy: rows disagree on the trade date")
    session = dates.pop()
    meta = {
        "catalysts_sourced": False,
        "weights_sourced": False,
        "session": session.isoformat(),
        "universe": sorted(wanted),
        "symbols_missing": sorted(wanted - set(found)),
    }
    return _session_close(session), [found[s] for s in sorted(found)], meta


PARTICIPANTS = ("Client", "DII", "FII", "Pro")
OI_TITLE = re.compile(r"as on ([A-Z][a-z]{2} \d{1,2}, \d{4})")
OI_COLUMNS = (
    "Future Index Long",
    "Future Index Short",
    "Future Stock Long",
    "Future Stock Short",
    "Option Index Call Long",
    "Option Index Put Long",
    "Option Index Call Short",
    "Option Index Put Short",
    "Option Stock Call Long",
    "Option Stock Put Long",
    "Option Stock Call Short",
    "Option Stock Put Short",
    "Total Long Contracts",
    "Total Short Contracts",
)

OI_PAIRS = (
    ("Future Index Long", "Future Index Short"),
    ("Future Stock Long", "Future Stock Short"),
    ("Option Index Call Long", "Option Index Call Short"),
    ("Option Index Put Long", "Option Index Put Short"),
    ("Option Stock Call Long", "Option Stock Call Short"),
    ("Option Stock Put Long", "Option Stock Put Short"),
)


def parse_participant_oi(text: str) -> tuple[datetime, list[dict], dict]:
    """fao_participant_oi_DDMMYYYY.csv: a title line "... as on Mon DD, YYYY", then
    Client Type + 14 contract columns, rows Client / DII / FII / Pro / TOTAL. Header
    cells may carry stray tabs. Each column must add up to the TOTAL row, each row's
    long / short columns to its totals, and market-wide longs must equal shorts. No contract
    level OI, settle prices or ban list are in this file."""
    title, _, body = text.lstrip("\ufeff").partition("\n")
    m = OI_TITLE.search(title)
    if not m:
        raise NseDataError("participant OI: no 'as on <date>' title line")
    session = _ist(m.group(1), "%b %d, %Y").date()
    rows = _csv_rows(body)
    by: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        who = row.get("Client Type", "")
        if who not in (*PARTICIPANTS, "TOTAL") or who in by:
            raise NseDataError(f"participant OI: unexpected or repeated row {who!r}")
        by[who] = {c: _count(row[c], f"{who} {c}") for c in OI_COLUMNS}
    if set(by) != {*PARTICIPANTS, "TOTAL"}:
        raise NseDataError(f"participant OI: rows {sorted(by)} incomplete")
    for c in OI_COLUMNS:
        if sum(by[p][c] for p in PARTICIPANTS) != by["TOTAL"][c]:
            raise NseDataError(f"participant OI: {c} does not add up to TOTAL")
    for who, v in by.items():
        for side in ("Long", "Short"):
            parts = sum(v[c] for c in OI_COLUMNS[:12] if c.endswith(side))
            if parts != v[f"Total {side} Contracts"]:
                raise NseDataError(f"participant OI: {who} Total {side} does not add up")
    total = by["TOTAL"]
    for long_col, short_col in OI_PAIRS:  # every open long has an open short
        if total[long_col] != total[short_col]:
            raise NseDataError(f"participant OI: TOTAL {long_col} != {short_col}")
    part = [
        {
            "participant": p,
            "net_contracts": by[p]["Future Index Long"] - by[p]["Future Index Short"],
            "index_fut_long": by[p]["Future Index Long"],
            "index_fut_short": by[p]["Future Index Short"],
        }
        for p in PARTICIPANTS
    ]
    meta = {"participant_oi": part, "session": session.isoformat(), "contract_oi_sourced": False}
    return _session_close(session), [], meta


PARSERS = {
    DatasetKind.PRE_OPEN: parse_pre_open,
    DatasetKind.OPTION_CHAIN: parse_option_chain,
    DatasetKind.FLOWS: parse_fii_dii,
    DatasetKind.SECTORS: parse_all_indices,
    DatasetKind.FNO: parse_participant_oi,
}


class NsePublicFeed:
    """FeedAdapter over NSE public-published data, read through an injected source."""

    def __init__(
        self, source: NseSource, stock_universe: tuple[str, ...] = SAMPLE_STOCK_UNIVERSE
    ) -> None:
        if not stock_universe:
            raise ValueError("stock universe must not be empty")
        self._source = source
        self._universe = tuple(stock_universe)
        self.name = f"nse_public:{source.name}"
        self.is_mock = source.is_sample
        self._problems: dict[DatasetKind, str] = {}

    def capabilities(self) -> Capabilities:
        caps = {
            kind: Capability(
                True,
                "NSE public published data (delayed / end-of-day pages)",
                terms_unconfirmed=TERMS_UNCONFIRMED,
            )
            for kind in FILES
        }
        caps |= {kind: Capability(False, why) for kind, why in NOT_SERVED.items()}
        return Capabilities(caps)

    def problems(self) -> dict[DatasetKind, str]:
        return dict(self._problems)

    @staticmethod
    def _source_label(kind: DatasetKind, sample: bool) -> Source:
        _, source_id, url = FILES[kind]
        name = "NSE public (sample shape)" if sample else "NSE public"
        return Source(id=source_id, name=name, url=url, is_mock=sample)

    def fetch(self, kind: DatasetKind, trading_date: date) -> Dataset | None:
        self._problems.pop(kind, None)
        if kind not in FILES:
            return None  # capability says why (not granted)
        file_name = FILES[kind][0]
        try:
            data = self._source.read(trading_date, file_name)
            if data is None:
                raise NseDataError(f"{file_name} not saved for {trading_date.isoformat()}")
            if file_name.endswith(".csv"):
                text = data.decode("utf-8-sig")  # Excel/Notepad saves add a BOM
                if kind is DatasetKind.STOCKS:
                    as_of, records, meta = parse_cm_bhavcopy(text, self._universe)
                else:
                    as_of, records, meta = PARSERS[kind](text)
            else:
                as_of, records, meta = PARSERS[kind](json.loads(data, parse_float=Decimal))
        except (NseDataError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
            self._problems[kind] = f"{file_name}: {type(exc).__name__}: {exc}"[:300]
            return None
        meta = {**meta, "adapter_version": NSE_ADAPTER_VERSION, "file": file_name}
        return Dataset(
            kind=kind,
            source=self._source_label(kind, self._source.is_sample),
            as_of=as_of,
            records=records,
            meta=meta,
        )
