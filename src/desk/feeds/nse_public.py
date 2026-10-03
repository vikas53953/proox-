"""NSE public-published-data adapter (G03 staged direction, step 2). DISABLED.

Serves three datasets from NSE's public pages: pre-open market (09:00-09:08 IST
indicative auction), index option chain, and FII/DII provisional cash activity. Every
other dataset is declared NOT granted, with the reason, so lenses show a gap and never
an invented value.

There is NO network code here. The adapter reads through an injected `source`; the only
source that exists is `FileSource`, which reads files saved on disk. While G03 is BLOCKED,
`config.load_settings` refuses DESK_FEED_ADAPTER=nse_public, so this only runs in tests.

Field names follow the public JSON shapes as best documented; the sample files are
hand-made (fixtures/nse_public/README.md) and must be re-checked against real files on
the owner's PC before G03 closes.
"""

import json
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
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
}

NOT_SERVED: dict[DatasetKind, str] = {
    DatasetKind.NEWS: "news is not NSE published market data; not served by NSE public adapter",
    DatasetKind.GLOBAL: "global markets are not NSE data; not served by NSE public adapter",
    DatasetKind.GIFT_NIFTY: "GIFT Nifty is an NSE IX product; not in NSE public adapter",
    DatasetKind.MACRO: "macro / commodities / FX are not served by NSE public adapter",
    DatasetKind.SECTORS: "sector indices not in NSE public adapter step 2 scope",
    DatasetKind.STOCKS: "stock closes / catalysts not in NSE public adapter step 2 scope",
    DatasetKind.INDEX_BARS: "intraday index bars are not part of NSE public published data",
    DatasetKind.FUT_BARS: "intraday futures bars are not part of NSE public published data",
    DatasetKind.DEPTH: "no licensed depth / spread statistics in NSE public data",
    DatasetKind.TRADES: "no trade-event / order-flow rights in NSE public data",
    DatasetKind.FNO: "F&O bhavcopy / participant OI not in NSE public adapter step 2 scope",
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
    near = expiries[0]
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
    as_of = datetime.combine(session, SESSION_CLOSE_IST, tzinfo=IST)
    return as_of, records, {"margin_financing_sourced": False, "session": session.isoformat()}


PARSERS = {
    DatasetKind.PRE_OPEN: parse_pre_open,
    DatasetKind.OPTION_CHAIN: parse_option_chain,
    DatasetKind.FLOWS: parse_fii_dii,
}


class NsePublicFeed:
    """FeedAdapter over NSE public-published data, read through an injected source."""

    def __init__(self, source: NseSource) -> None:
        self._source = source
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
            raw = json.loads(data, parse_float=Decimal)
            as_of, records, meta = PARSERS[kind](raw)
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
