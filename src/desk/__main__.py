"""Operator command: print a MOCK report as text.

python -m desk report --scenario full_mock [--kind AUCTION] [--date 2026-10-01]
"""

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from desk.agents.model import MockModelAdapter
from desk.config import load_settings
from desk.db.session import make_engine, make_session_factory
from desk.feeds.fixture import FixtureFeed
from desk.lenses.context import ReportKind
from desk.market_calendar import TradingCalendar
from desk.onboarding.invites import create_invite
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
    inv = sub.add_parser("invite", help="create an invite (operator only)")
    inv.add_argument("--phone-number-id", required=True)
    inv.add_argument("--bound-sender", default=None)
    inv.add_argument("--no-code", action="store_true", help="preapproved sender: Hi is enough")
    inv.add_argument("--ttl-hours", type=int, default=72)
    args = parser.parse_args()
    if args.cmd == "invite":
        _invite(args)
        return

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


def _invite(args: argparse.Namespace) -> None:
    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DESK_DATABASE_URL is not set")
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as session:
        invite, code = create_invite(
            session,
            business_phone_id=args.phone_number_id,
            now=datetime.now(UTC),
            ttl=timedelta(hours=args.ttl_hours),
            bound_sender=args.bound_sender,
            with_code=not args.no_code,
        )
        session.commit()
    print(f"invite {invite.id} expires {invite.expires_at:%Y-%m-%d %H:%M} UTC")
    if code:
        print(f"code (shown once, not stored): {code}")
    print("Nothing was sent. Share the official bot number + code yourself.")


if __name__ == "__main__":
    main()
