"""Price bars and the no-look-ahead rule: only bars that ended by the cutoff are usable."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from desk.market_calendar import IST


@dataclass(frozen=True)
class Bar:
    instrument: str
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None  # the cash index has no traded volume -> None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("bar timestamps must be timezone-aware")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError(f"inconsistent OHLC in bar {self.start}")

    @property
    def session(self) -> date:
        return self.start.astimezone(IST).date()


def completed_bars(bars: list[Bar], cutoff: datetime) -> list[Bar]:
    """Bars fully finished at `cutoff`. An in-progress bar is never used (no leakage)."""
    return [b for b in bars if b.end <= cutoff]


def latest_completed_session(bars: list[Bar], cutoff: datetime) -> list[Bar]:
    done = completed_bars(bars, cutoff)
    if not done:
        return []
    last = max(b.session for b in done)
    return sorted((b for b in done if b.session == last), key=lambda b: b.start)
