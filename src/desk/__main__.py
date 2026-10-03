"""Operator command: print a MOCK report as text.

python -m desk report --scenario full_mock [--kind AUCTION] [--date 2026-10-01]
python -m desk senders status|encrypt|rotate   (B02/B06: transport ids at rest, operator only)
python -m desk nse verify <folder>   (offline: check hand-saved NSE files; adapter stays off)
"""

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from desk.agents.model import MockModelAdapter
from desk.config import SettingsError, load_settings
from desk.db.session import make_engine, make_session_factory
from desk.feeds.fixture import FixtureFeed
from desk.lenses.context import ReportKind
from desk.logsafe import install as install_log_redaction
from desk.market_calendar import TradingCalendar
from desk.onboarding.invites import create_invite
from desk.pii import check_startup, configure_from
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
    snd = sub.add_parser("senders", help="B02: transport ids at rest (operator only)")
    snd.add_argument("action", choices=["status", "encrypt", "rotate"])
    nse = sub.add_parser("nse", help="NSE public files (offline, operator only)")
    nse.add_argument("action", choices=["verify"])
    nse.add_argument("folder", help="folder with files saved by hand from NSE")
    nse.add_argument(
        "--universe", default=None, help="bhavcopy symbols to check (comma list); default all EQ"
    )
    args = parser.parse_args()
    install_log_redaction()
    if args.cmd == "nse":
        _nse_verify(args)
        return
    if args.cmd == "senders":
        _senders(args)
        return
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


def _nse_verify(args: argparse.Namespace) -> None:
    """Offline: no settings, no database, no network; the adapter is not enabled."""
    from desk.feeds.nse_verify import render, verify_folder

    folder = Path(args.folder)
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    universe = None
    if args.universe is not None:
        universe = tuple(s.strip() for s in args.universe.split(",") if s.strip())
        if not universe:
            raise SystemExit("--universe needs at least one symbol")
    lines, ok = render(*verify_folder(folder, universe))
    print("\n".join(lines))
    if not ok:
        raise SystemExit(1)


def _factory(check: bool = True):
    from desk.pii import SenderKeyError, SenderStateError

    try:
        settings = load_settings()
        configure_from(settings)  # B02: once, before any DB access
    except (SenderKeyError, SettingsError) as exc:
        raise SystemExit(f"refusing to start: {exc}") from None
    if not settings.database_url:
        raise SystemExit("DESK_DATABASE_URL is not set")
    factory = make_session_factory(make_engine(settings.database_url))
    if check:
        try:
            with factory() as s:
                check_startup(s)
        except SenderStateError as exc:
            raise SystemExit(f"refusing to start: {exc}") from None
    return settings, factory


def _senders(args: argparse.Namespace) -> None:
    """status: plaintext / encrypted row counts per covered column (mixed = flagged).
    encrypt: rewrite every plaintext transport id, all tables in ONE transaction;
    idempotent; refused unless DESK_ENCRYPT_SENDERS=1 with a valid DESK_SENDER_KEY.
    rotate (B06): re-encrypt every value from DESK_SENDER_KEY to DESK_SENDER_KEY_NEW in
    ONE transaction; safe to re-run; the operator then swaps the env vars."""
    from sqlalchemy.exc import IntegrityError

    from desk.pii import enabled, encrypt_existing, storage_state

    settings, factory = _factory(check=False)
    with factory() as s:
        if args.action == "rotate":
            _rotate(s, settings)
        if args.action == "encrypt":
            if not enabled():
                raise SystemExit("refusing: set DESK_ENCRYPT_SENDERS=1 and DESK_SENDER_KEY")
            try:
                changed = encrypt_existing(s)
                s.commit()
            except IntegrityError:
                s.rollback()
                raise SystemExit(
                    "refusing: the same id is stored both plain and encrypted in a unique "
                    "column; nothing was changed (resolve those rows first)"
                ) from None
            print(f"encrypted {sum(changed.values())} values; nothing else changed")
        state = storage_state(s)
    plain = sum(p for p, _ in state.values())
    enc = sum(e for _, e in state.values())
    for (table, col), (p, e) in state.items():
        print(f"{table}.{col}: plain={p} encrypted={e}")
    flag = "ON" if enabled() else "OFF"
    mixed = (enabled() and plain) or (not enabled() and enc)
    print(f"flag {flag}; " + ("MISMATCH: rows do not match the flag" if mixed else "consistent"))
    if mixed:
        raise SystemExit(1)


def _rotate(session, settings) -> None:
    import os

    from sqlalchemy.exc import IntegrityError

    from desk.pii import SenderKeyError, SenderStateError, parse_key, register_key, rotate_existing

    if not settings.senders.enabled:
        raise SystemExit("refusing: rotate needs DESK_ENCRYPT_SENDERS=1 and DESK_SENDER_KEY")
    old = settings.senders.key
    try:
        new = parse_key(os.environ.get("DESK_SENDER_KEY_NEW", ""), "DESK_SENDER_KEY_NEW")
        register_key(new)
        rotated, skipped = rotate_existing(session, old, new)
        session.commit()
    except (SenderKeyError, SenderStateError) as exc:
        session.rollback()
        raise SystemExit(f"refusing: {exc}") from None
    except IntegrityError:
        session.rollback()
        raise SystemExit(
            "refusing: the same id is stored under both keys in a unique column; "
            "nothing was changed (resolve those rows first)"
        ) from None
    print(f"rotated {rotated} values; {skipped} already under the new key")
    print("now set DESK_SENDER_KEY to the new key and clear DESK_SENDER_KEY_NEW")


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
    from dataclasses import replace

    from desk.agents.model import MockModelAdapter
    from desk.config import GateBlockedError
    from desk.jobs.worker import Deps
    from desk.runner import PollBackoff, describe, run_cycle, serve_loop
    from desk.transport.telegram.client import (
        TelegramFatalError,
        TelegramPollError,
        make_telegram_transport,
    )
    from desk.transport.whatsapp.adapter import WhatsAppTransport
    from desk.transport.whatsapp.client import MOCK_PHONE_NUMBER_ID, make_send_client
    from desk.transport.whatsapp.templates import TemplateRegistry

    settings, factory = _factory()
    calendar = TradingCalendar.load(FIXTURES / "calendar" / "NSE-CM-holidays-2026-v1.json")
    try:  # fake transports refuse to start while a real token is set (HIGH-1, B08)
        wa_client = make_send_client(settings)
        tg_transport, tg_client = make_telegram_transport(settings)
    except GateBlockedError as exc:
        raise SystemExit(f"refusing to start: {exc}") from None
    if settings.whatsapp.phone_number_id not in ("", MOCK_PHONE_NUMBER_ID):
        print("note: WHATSAPP_PHONE_NUMBER_ID is set; its rows stay queued (G02: fake only)")
    if settings.telegram.live:
        try:
            me = tg_client.get_me()
        except TelegramPollError as exc:
            raise SystemExit(f"Telegram getMe failed: {describe(exc)}") from None
        if str(me.get("id")) != settings.telegram.bot_id:
            raise SystemExit("bot token and getMe disagree; check TELEGRAM_BOT_TOKEN")
        print(f"Telegram live as @{me.get('username')} (Bot API pin checked)")
    deps = Deps(
        calendar=calendar,
        model=MockModelAdapter(),
        # G03 open: MOCK fixtures only; other days show every lens as UNAVAILABLE
        feed_for=lambda day: FixtureFeed(FIXTURES / "market", date(2026, 10, 1), "auction_mock"),
    )
    transports = {"whatsapp": WhatsAppTransport(wa_client), "telegram": tg_transport}
    templates = TemplateRegistry.load(FIXTURES.parent / "config" / "whatsapp_templates.json")
    # fake mode polls the fake bot; live mode the real one (same id as the transport)
    tg_settings = (
        settings.telegram
        if settings.telegram.live
        else replace(settings.telegram, bot_token=f"{tg_transport.endpoint}:MOCK")
    )

    backoff = PollBackoff()

    def cycle():
        return run_cycle(
            factory,
            deps=deps,
            transports=transports,
            templates=templates,
            now=datetime.now(UTC),
            telegram=(tg_client, tg_settings),
            auction_enabled=args.auction,
            poll_timeout_s=0 if args.once else min(args.interval, 25),
            poll_backoff=backoff,
        )

    def log(line: str) -> None:
        print(f"{datetime.now(UTC):%H:%M:%S}Z {line}", flush=True)

    try:
        serve_loop(
            cycle,
            log=log,
            sleep=time.sleep,
            # live: the long poll itself waits for messages; fake: getUpdates returns at once
            pause_s=1 if settings.telegram.live else args.interval,
            max_cycles=1 if args.once else None,
        )
    except TelegramFatalError as exc:
        raise SystemExit(f"stopping: {describe(exc)}") from None


if __name__ == "__main__":
    main()
