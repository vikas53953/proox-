"""Prior-session high / low / close and range (R06)."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from desk.quant.bars import Bar, latest_completed_session

LEVELS_VERSION = "levels-v1"


@dataclass(frozen=True)
class Levels:
    session: date
    high: Decimal
    low: Decimal
    close: Decimal
    range_points: Decimal
    close_location: Decimal  # 0 = closed at low, 1 = closed at high
    bars_used: int
    version: str = LEVELS_VERSION


def prior_session_levels(bars: list[Bar], cutoff: datetime) -> Levels | None:
    session = latest_completed_session(bars, cutoff)
    if not session:
        return None
    high = max(b.high for b in session)
    low = min(b.low for b in session)
    close = session[-1].close
    rng = high - low
    loc = ((close - low) / rng).quantize(Decimal("0.01")) if rng else Decimal("0.50")
    return Levels(session[0].session, high, low, close, rng, loc, len(session))
