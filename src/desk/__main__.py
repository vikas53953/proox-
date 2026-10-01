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
from desk.logsafe import install as install_log_redaction
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
    rep.add_argument(
        "--media-dir", default=None, help="also write the PDF, chart PNG and chart manifest here"
    )
    inv = sub.add_parser("invite", help="create an invite (operator only)")
    inv.add_argument("--channel", default="whatsapp", choices=["whatsapp", "telegram"])
    inv.add_argument("--phone-number-id", default=None, help="WhatsApp only")
    inv.add_argument("--bound-sender", default=None)
    inv.add_argument("--no-code", action="store_true", help="preapproved sender: Hi is enough")
    inv.add_argument("--ttl-hours", type=int, default=72)
    srv = sub.add_parser("serve", help="PC test loop: poll Telegram, plan, work, send")
    srv.add_argument("--once", action="store_true")
    srv.add_argument("--interval", type=int, default=30, help="seconds between cycles")
    srv.add_argument("--auction", action="store_true", help="enable the 09:12 addendum")
    args = parser.parse_args()
    install_log_redaction()
    if args.cmd == "invite":
        _invite(args)
        return
    if args.cmd == "serve":
        _serve(args)
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
    if isinstance(result, NoReport):
        print(f"NO REPORT: {result.reason}")
        return
    print(render_text(result))
    if args.media_dir:
        _write_media(result, Path(args.media_dir))


def _write_media(report, out: Path) -> None:
    import json

    from desk.render.charts import report_charts
    from desk.render.pdf import render_pdf

    out.mkdir(parents=True, exist_ok=True)
    charts = report_charts(report)
    stem = f"{report.id}-v{report.version}"
    (out / f"{stem}.pdf").write_bytes(render_pdf(report, charts))
    for c in charts:
        (out / f"{stem}-{c.name}.png").write_bytes(c.png)
        (out / f"{stem}-{c.name}.manifest.json").write_text(json.dumps(c.manifest, indent=2))


def _factory():
    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DESK_DATABASE_URL is not set")
    return settings, make_session_factory(make_engine(settings.database_url))


def _invite(args: argparse.Namespace) -> None:
    from desk.onboarding.invites import telegram_deep_link

    settings, factory = _factory()
    if args.channel == "telegram":
        tg = settings.telegram
        if not tg.bot_username or tg.bot_id == "0":
            raise SystemExit("set TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME first")
        business_id, with_code, bound = tg.bot_id, True, None
    else:
        if not args.phone_number_id:
            raise SystemExit("--phone-number-id is required for WhatsApp")
        business_id, with_code, bound = args.phone_number_id, not args.no_code, args.bound_sender
    with factory() as session:
        invite, code = create_invite(
            session,
            business_phone_id=business_id,
            now=datetime.now(UTC),
            ttl=timedelta(hours=args.ttl_hours),
            bound_sender=bound,
            with_code=with_code,
            channel=args.channel,
        )
        session.commit()
    print(
        f"{args.channel} invite {invite.id} expires {invite.expires_at:%Y-%m-%d %H:%M} UTC "
        "(single use)"
    )
    if code and args.channel == "telegram":
        link = telegram_deep_link(settings.telegram.bot_username, code)
        print(f"link (shown once, not stored): {link}")
    elif code:
        print(f"code (shown once, not stored): {code}")
    print("Nothing was sent. Share it yourself.")


def _serve(args: argparse.Namespace) -> None:
    import time

    from desk.agents.model import MockModelAdapter
    from desk.jobs.worker import Deps
    from desk.runner import run_cycle
    from desk.transport.telegram.client import make_telegram_transport
    from desk.transport.whatsapp.adapter import WhatsAppTransport
    from desk.transport.whatsapp.client import make_client
    from desk.transport.whatsapp.templates import TemplateRegistry

    settings, factory = _factory()
    calendar = TradingCalendar.load(FIXTURES / "calendar" / "NSE-CM-holidays-2026-v1.json")
    tg_transport, tg_client = make_telegram_transport(settings)
    if settings.telegram.live:
        me = tg_client.get_me()
        if str(me.get("id")) != settings.telegram.bot_id:
            raise SystemExit("bot token and getMe disagree; check TELEGRAM_BOT_TOKEN")
        print(f"Telegram live as @{me.get('username')} (Bot API pin checked)")
    deps = Deps(
        calendar=calendar,
        model=MockModelAdapter(),
        # G03 open: MOCK fixtures only; other days show every lens as UNAVAILABLE
        feed_for=lambda day: FixtureFeed(FIXTURES / "market", date(2026, 10, 1), "auction_mock"),
    )
    transports = {"whatsapp": WhatsAppTransport(make_client(settings)), "telegram": tg_transport}
    templates = TemplateRegistry.load(FIXTURES.parent / "config" / "whatsapp_templates.json")
    while True:
        report = run_cycle(
            factory,
            deps=deps,
            transports=transports,
            templates=templates,
            now=datetime.now(UTC),
            telegram=(tg_client, settings.telegram),
            auction_enabled=args.auction,
            poll_timeout_s=0 if args.once else min(args.interval, 25),
        )
        print(
            f"{datetime.now(UTC):%H:%M:%S}Z poll={report.poll} plan={report.plan} "
            f"work={report.work} send={report.send}"
        )
        if args.once:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
