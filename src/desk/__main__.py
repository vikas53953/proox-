"""Operator command: print a MOCK report as text.

python -m desk report --scenario full_mock [--kind AUCTION] [--date 2026-10-01]
"""

import argparse
from datetime import date
from pathlib import Path

from desk.agents.model import MockModelAdapter
from desk.feeds.fixture import FixtureFeed
from desk.lenses.context import ReportKind
from desk.market_calendar import TradingCalendar
from desk.pipeline import NoReport, run_report
from desk.report.text import render_text

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def main() -> None:
    parser = argparse.ArgumentParser(prog="desk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="print a MOCK report from fixtures")
    rep.add_argument("--scenario", default="full_mock")
    rep.add_argument("--kind", default="MORNING", choices=[k.value for k in ReportKind])
    rep.add_argument("--date", default="2026-10-01")
    args = parser.parse_args()

    day = date.fromisoformat(args.date)
    calendar = TradingCalendar.load(FIXTURES / "calendar" / "NSE-CM-holidays-2026-v1.json")
    result = run_report(
        calendar=calendar,
        feed=FixtureFeed(FIXTURES / "market", date(2026, 10, 1), args.scenario),
        model=MockModelAdapter(),
        trading_date=day,
        kind=ReportKind(args.kind),
    )
    print(f"NO REPORT: {result.reason}" if isinstance(result, NoReport) else render_text(result))


if __name__ == "__main__":
    main()
