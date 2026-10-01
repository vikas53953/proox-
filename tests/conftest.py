from datetime import date
from pathlib import Path

import pytest

from desk.agents.model import MockModelAdapter
from desk.feeds.fixture import FixtureFeed
from desk.lenses.context import ReportKind
from desk.market_calendar import TradingCalendar
from desk.pipeline import run_report

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
MOCK_DAY = date(2026, 10, 1)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def mock_calendar() -> TradingCalendar:
    return TradingCalendar.load(FIXTURES / "calendar" / "MOCK-test-calendar-2026.json")


def feed(scenario: str, day: date = MOCK_DAY) -> FixtureFeed:
    return FixtureFeed(FIXTURES / "market", day, scenario)


@pytest.fixture(scope="session")
def build_report(mock_calendar):
    def _build(
        scenario: str = "full_mock", kind: ReportKind = ReportKind.MORNING, day: date = MOCK_DAY
    ):
        return run_report(
            calendar=mock_calendar,
            feed=feed(scenario),
            model=MockModelAdapter(),
            trading_date=day,
            kind=kind,
        )

    return _build
