"""Shared inputs for every lens, plus freshness rules (no stale-as-fresh, no look-ahead)."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from desk.core.facts import DataClass, Fact, Gap, Source
from desk.core.lens import LensId, LensResult
from desk.feeds.base import Capabilities, Dataset, DatasetKind
from desk.market_calendar import IST, fmt_ist

if TYPE_CHECKING:
    from desk.agents.model import ModelAdapter


class ReportKind(StrEnum):
    MORNING = "MORNING"  # 08:45 IST
    AUCTION = "AUCTION"  # optional 09:12 IST indicative-auction addendum


# Datasets that must describe the latest completed session (the previous trading day).
PRIOR_SESSION_KINDS = frozenset(
    {
        DatasetKind.SECTORS,
        DatasetKind.STOCKS,
        DatasetKind.INDEX_BARS,
        DatasetKind.FUT_BARS,
        DatasetKind.DEPTH,
        DatasetKind.TRADES,
        DatasetKind.FNO,
        DatasetKind.FLOWS,
        DatasetKind.OPTION_CHAIN,
    }
)
# Rolling datasets: maximum age at the cutoff.
MAX_AGE: dict[DatasetKind, timedelta] = {
    DatasetKind.NEWS: timedelta(hours=18),
    DatasetKind.MACRO: timedelta(hours=20),
    DatasetKind.GIFT_NIFTY: timedelta(minutes=30),
    # Indicative auction data must be from this pre-open (09:00-09:08 IST), never yesterday's.
    DatasetKind.PRE_OPEN: timedelta(minutes=15),
}
GLOBAL_MAX_AGE = {"closed": timedelta(hours=20), "open": timedelta(minutes=30)}


class LookAheadError(ValueError):
    """Data timestamped after the report cutoff — must never enter a report."""


@dataclass
class LensContext:
    trading_date: date
    prev_trading_date: date
    cutoff: datetime
    kind: ReportKind
    capabilities: Capabilities
    datasets: dict[DatasetKind, Dataset | None]
    model: "ModelAdapter | None" = None
    results: dict[LensId, LensResult] = field(default_factory=dict)
    # Why the feed returned no data for a dataset (malformed / missing file), if it said.
    fetch_problems: dict[DatasetKind, str] = field(default_factory=dict)

    def ds(self, kind: DatasetKind) -> Dataset | None:
        ds = self.datasets.get(kind)
        if ds is not None and ds.as_of > self.cutoff:
            raise LookAheadError(f"{kind} as_of {ds.as_of} is after cutoff {self.cutoff}")
        return ds

    def age(self, ts: datetime) -> timedelta:
        return self.cutoff - ts

    def staleness(self, kind: DatasetKind, ds: Dataset) -> str | None:
        """None if fresh; else a plain reason naming the age / wrong session."""
        if kind in PRIOR_SESSION_KINDS:
            session = ds.as_of.astimezone(IST).date()
            if session != self.prev_trading_date:
                return (
                    f"data is from {session.isoformat()}, expected prior session "
                    f"{self.prev_trading_date.isoformat()}"
                )
            return None
        limit = MAX_AGE.get(kind)
        if limit is not None and self.age(ds.as_of) > limit:
            return f"as of {fmt_ist(ds.as_of)}, {_fmt_age(self.age(ds.as_of))} old"
        return None


def _fmt_age(td: timedelta) -> str:
    hours, rem = divmod(int(td.total_seconds()), 3600)
    return f"{hours}h{rem // 60:02d}m"


def fmt_age(td: timedelta) -> str:
    return _fmt_age(td)


def missing(kind: DatasetKind, ctx: LensContext, effect: str = "") -> Gap:
    cap = ctx.capabilities.get(kind)
    if not cap.granted:
        reason = f"{kind.value}: not available under current feed rights ({cap.rights_note})"
    elif kind in ctx.fetch_problems:
        reason = (
            f"{kind.value}: feed could not read data for {ctx.trading_date.isoformat()} "
            f"({ctx.fetch_problems[kind]})"
        )
    else:
        reason = f"{kind.value}: feed returned no data for {ctx.trading_date.isoformat()}"
    return Gap(topic=kind.value, data_class=DataClass.UNAVAILABLE, reason=reason, effect=effect)


def stale_gap(kind: DatasetKind, why: str, effect: str = "") -> Gap:
    return Gap(topic=kind.value, data_class=DataClass.STALE, reason=why, effect=effect)


def dec(v: Any) -> Decimal:
    if isinstance(v, float):
        raise TypeError("float in fixture; use string or Decimal")
    return Decimal(str(v))


def mk_fact(
    ds: Dataset,
    label: str,
    value: Decimal | str,
    unit: str,
    instrument: str,
    data_class: DataClass,
    note: str = "",
    source: Source | None = None,
    as_of: datetime | None = None,
) -> Fact:
    return Fact(
        label=label,
        value=value,
        unit=unit,
        instrument=instrument,
        source=source or ds.source,
        as_of=as_of or ds.as_of,
        data_class=data_class,
        note=note,
    )


def session_fact_class(ctx: LensContext, kind: DatasetKind, ds: Dataset) -> tuple[DataClass, str]:
    """PRIOR SESSION if it is the expected session, else STALE with the reason."""
    why = ctx.staleness(kind, ds)
    return (DataClass.STALE, why) if why else (DataClass.PRIOR_SESSION, "")
