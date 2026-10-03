"""Trading calendar: which dates get a report, and the IST cutoffs (08:45 / 09:12).

Storage is UTC; display is Asia/Kolkata. The calendar is a dated, versioned file taken
from NSE's official holiday list (S30). A date outside the file's coverage raises —
we never guess whether a day is a trading day.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CALENDAR_LOGIC_VERSION = "cal-v1"

MORNING_CUTOFF_IST = time(8, 45)
AUCTION_CUTOFF_IST = time(9, 12)
PRE_OPEN_START_IST = time(9, 0)
CONTINUOUS_START_IST = time(9, 15)


class CalendarNotCoveredError(LookupError):
    """The date is outside the calendar file's coverage — refuse rather than guess."""


@dataclass(frozen=True)
class TradingCalendar:
    version: str
    segment: str
    source_url: str
    retrieved_at: datetime
    is_mock: bool
    covers_from: date
    covers_to: date
    holidays: frozenset[date]
    special_sessions: dict[date, str]  # date -> timing in IST, or "TBD"

    @classmethod
    def load(cls, path: Path) -> "TradingCalendar":
        raw = json.loads(path.read_text())
        holidays = [date.fromisoformat(h["date"]) for h in raw["holidays"]]
        weekend = [d for d in holidays if d.weekday() >= 5]
        if weekend:
            raise ValueError(f"weekday-holiday list contains weekend dates: {weekend}")
        return cls(
            version=raw["version"],
            segment=raw["segment"],
            source_url=raw["source_url"],
            retrieved_at=datetime.fromisoformat(raw["retrieved_at"]).astimezone(UTC),
            is_mock=bool(raw["is_mock"]),
            covers_from=date.fromisoformat(raw["covers_from"]),
            covers_to=date.fromisoformat(raw["covers_to"]),
            holidays=frozenset(holidays),
            special_sessions={
                date.fromisoformat(s["date"]): s.get("timing_ist", "TBD")
                for s in raw.get("special_sessions", [])
            },
        )

    def is_special_session(self, day: date) -> bool:
        return day in self.special_sessions

    def is_trading_day(self, day: date) -> bool:
        if not self.covers_from <= day <= self.covers_to:
            raise CalendarNotCoveredError(f"{day} outside calendar {self.version}")
        if day in self.special_sessions:
            return True
        return day.weekday() < 5 and day not in self.holidays


def ist_to_utc(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=IST).astimezone(UTC)


def morning_cutoff_utc(day: date) -> datetime:
    return ist_to_utc(day, MORNING_CUTOFF_IST)


def auction_cutoff_utc(day: date) -> datetime:
    return ist_to_utc(day, AUCTION_CUTOFF_IST)


def fmt_ist(ts: datetime) -> str:
    return ts.astimezone(IST).strftime("%d %b %Y %H:%M IST")


def previous_trading_day(cal: TradingCalendar, day: date) -> date:
    """Most recent trading day strictly before `day` (raises if outside coverage)."""
    probe = day
    for _ in range(31):
        probe = date.fromordinal(probe.toordinal() - 1)
        if cal.is_trading_day(probe):
            return probe
    raise CalendarNotCoveredError(f"no trading day within 31 days before {day}")
