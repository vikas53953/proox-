"""FeedAdapter interface and the capability manifest (RC07).

A lens may only produce a metric if the active feed declares the right to it.
Example: no licensed trade events with aggressor side -> no real order-flow delta.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol

from desk.core.facts import Source

# Gap topic for unconfirmed usage terms; the only R15 gap repeated in the top-gaps list.
TERMS_GAP_TOPIC = "data usage terms"


class DatasetKind(StrEnum):
    NEWS = "news"
    GLOBAL = "global"
    GIFT_NIFTY = "gift_nifty"
    MACRO = "macro"
    SECTORS = "sectors"
    STOCKS = "stocks"
    INDEX_BARS = "index_bars"
    FUT_BARS = "fut_bars"
    DEPTH = "depth"
    TRADES = "trades"
    FNO = "fno"
    FLOWS = "flows"
    OPTION_CHAIN = "option_chain"
    PRE_OPEN = "pre_open"


@dataclass(frozen=True)
class Capability:
    granted: bool
    rights_note: str
    tier: str = ""  # e.g. depth "L1" / "L2-5-levels"; trades "with-aggressor" / "price-only"
    # Non-empty = the data may be read, but its usage / redistribution / AI-use terms are
    # not confirmed by the owner. R15 shows it as a gap and it is listed in the top gaps.
    terms_unconfirmed: str = ""


@dataclass(frozen=True)
class Capabilities:
    by_kind: dict[DatasetKind, Capability] = field(default_factory=dict)

    def get(self, kind: DatasetKind) -> Capability:
        return self.by_kind.get(kind, Capability(False, "not declared by feed"))


@dataclass(frozen=True)
class Dataset:
    kind: DatasetKind
    source: Source
    as_of: datetime
    records: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


class FeedAdapter(Protocol):
    name: str
    is_mock: bool

    def capabilities(self) -> Capabilities: ...

    def fetch(self, kind: DatasetKind, trading_date: date) -> Dataset | None:
        """Return the dataset, or None if the feed has nothing for it."""
        ...


def fetch_problems(feed: object) -> dict["DatasetKind", str]:
    """Why a feed returned None, per dataset, if it can say (optional `problems()`:
    e.g. a malformed or missing file). Feeds without it (the fixture feed) report none."""
    problems = getattr(feed, "problems", None)
    return dict(problems()) if callable(problems) else {}
