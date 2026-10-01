"""Channel rules decided right before each send (RC11, S54, S79).

- Replies: only inside the 24-hour customer-service window.
- Proactive (report parts, failure notices): only for opted-in tenants, only for TODAY's
  trading date (never yesterday-as-today), and only inside the window — or, outside it,
  after an APPROVED "report_ready" template has re-opened it. With no approved template
  (G02) the message waits safely; nothing is forced through.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.orm import Session

from desk.db.models import Inbound, Outbox, Tenant
from desk.market_calendar import IST
from desk.transport.whatsapp.templates import Template, TemplateRegistry

WINDOW = timedelta(hours=24)
TEMPLATE_KIND = "template_report_ready"


class Action(StrEnum):
    SEND_TEXT = "SEND_TEXT"
    SEND_TEMPLATE = "SEND_TEMPLATE"
    WAIT = "WAIT"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str = ""
    template: Template | None = None
    request_template: bool = False  # WAIT + queue one report_ready template


def window_open(last_inbound: datetime | None, now: datetime) -> bool:
    return last_inbound is not None and now - last_inbound < WINDOW


def ist_today(now: datetime) -> date:
    return now.astimezone(IST).date()


def decide(
    session: Session, row: Outbox, tenant: Tenant | None, now: datetime, templates: TemplateRegistry
) -> Decision:
    if not row.proactive:
        if tenant is not None:
            last = tenant.last_inbound_at
        else:  # reply to someone who is not a tenant: use that message's own time
            inbound = session.get(Inbound, row.in_reply_to) if row.in_reply_to else None
            last = inbound.provider_time if inbound else None
        if window_open(last, now):
            return Decision(Action.SEND_TEXT)
        return Decision(Action.CANCEL, "reply window (24h) has passed")

    if tenant is None or not tenant.report_opt_in:
        return Decision(Action.CANCEL, "not opted in to daily updates (or STOP)")
    if row.trading_date is not None and row.trading_date != ist_today(now):
        return Decision(
            Action.CANCEL,
            f"stale: for {row.trading_date}, today is {ist_today(now)}; never sent as today's",
        )
    if row.kind == TEMPLATE_KIND:
        tpl = templates.usable("report_ready")
        if tpl is None:
            return Decision(Action.WAIT, templates.why_not("report_ready"))
        return Decision(Action.SEND_TEMPLATE, template=tpl)
    if window_open(tenant.last_inbound_at, now):
        return Decision(Action.SEND_TEXT)
    if templates.usable("report_ready") is None:
        return Decision(Action.WAIT, "outside 24h window; " + templates.why_not("report_ready"))
    return Decision(
        Action.WAIT,
        "outside 24h window; report-ready template sent, waiting for a reply",
        request_template=True,
    )


def release_waiting(session: Session, tenant_id, now: datetime) -> int:
    """Customer wrote to us -> window open -> waiting messages may be tried again.
    Policy is re-checked at send time, so stale or opted-out items still get cancelled."""
    res = session.execute(
        update(Outbox)
        .where(
            Outbox.tenant_id == tenant_id,
            Outbox.state == "WAITING_WINDOW",
            Outbox.kind != TEMPLATE_KIND,
        )
        .values(state="PENDING", wait_reason=None, next_attempt_at=now)
    )
    return res.rowcount
