"""Handle one inbound WhatsApp message inside one database transaction.

Order: business-number check -> durable dedupe (message_id) -> existing tenant? resume :
try to bind an invite -> reply via outbox. Binding is atomic: the invite row is locked,
consumed and the tenant + agent binding created in the same transaction, so replays and
parallel webhooks can never create a second tenant or a second welcome (RC02).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from desk import pii
from desk.config import TelegramSettings, WhatsAppSettings
from desk.core.lens import LensId
from desk.db.models import (
    AgentBinding,
    Inbound,
    Invite,
    MessageBody,
    Outbox,
    StoredReport,
    Tenant,
)
from desk.feedback import FEEDBACK_ACK, parse_feedback, record_feedback
from desk.onboarding import messages as msgs
from desk.onboarding.invites import find_code, hash_code, redact
from desk.outbox.parts import text_index, text_parts, text_section
from desk.outbox.policy import ist_today, release_waiting
from desk.outbox.sender import cancel_pending_proactive, opt_out_blocked
from desk.render.charts import report_charts
from desk.report.model import Report
from desk.transport.whatsapp.payload import InboundMessage

NEUTRAL_WINDOW = timedelta(hours=24)
YES_WORDS = {"YES", "Y", "HAAN", "HA", "HAN"}
NO_WORDS = {"NO", "N", "NAHI", "NAHIN"}
GREETINGS = {"HI", "HII", "HELLO", "HEY", "NAMASTE", "/START"}  # /start = Telegram deep link


@dataclass(frozen=True)
class Outcome:
    handled_as: str
    tenant_id: uuid.UUID | None = None


def _queue(
    session: Session,
    msg: InboundMessage,
    tenant: Tenant | None,
    kind: str,
    body: str,
    now: datetime,
    key: str | None = None,
) -> None:
    session.execute(
        pg_insert(Outbox)
        .values(
            id=uuid.uuid4(),
            idempotency_key=key or f"{msg.message_id}:{kind}",
            tenant_id=tenant.id if tenant else None,
            channel=msg.channel,
            business_phone_id=msg.phone_number_id,
            recipient=msg.sender,
            kind=kind,
            body=body,
            state="PENDING",
            created_at=now,
            in_reply_to=msg.message_id,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )


def _find_tenant(session: Session, msg: InboundMessage) -> Tenant | None:
    return session.execute(
        select(Tenant).where(
            Tenant.channel == msg.channel,
            Tenant.business_phone_id == msg.phone_number_id,
            Tenant.sender == msg.sender,
        )
    ).scalar_one_or_none()


def _try_bind(
    session: Session, msg: InboundMessage, now: datetime, require_prebind: bool = False
) -> Tenant | None:
    """A pre-bound invite (bound_sender set) matches only its own sender. With
    TELEGRAM_REQUIRE_PREBIND=1 an unbound Telegram invite matches nobody. No match =
    the caller's neutral reply, and the invite stays open (no information leak)."""
    code = find_code(msg.text)
    conds = [
        Invite.state == "open",
        Invite.expires_at > now,
        Invite.business_phone_id == msg.phone_number_id,
        Invite.channel == msg.channel,
    ]
    if code:
        conds += [
            Invite.token_hash == hash_code(code),
            (Invite.bound_sender.is_(None)) | (Invite.bound_sender == msg.sender),
        ]
    else:  # preapproved sender: plain "Hi" is enough
        conds += [Invite.token_hash.is_(None), Invite.bound_sender == msg.sender]
    if require_prebind and msg.channel == "telegram":
        conds.append(Invite.bound_sender.is_not(None))
    invite = session.execute(
        select(Invite).where(*conds).order_by(Invite.created_at).limit(1).with_for_update()
    ).scalar_one_or_none()  # blocks on a concurrent consumer, then re-checks state
    if invite is None:
        return None
    tenant = Tenant(
        id=uuid.uuid4(),
        channel=msg.channel,
        business_phone_id=msg.phone_number_id,
        sender=msg.sender,
        opt_in_state="asked",
        state="pending",
        pending_reason=msgs.PENDING_REASON,
        created_at=now,
        last_inbound_at=msg.provider_time,
    )
    try:
        with session.begin_nested():
            session.add(tenant)
            session.flush()
    except IntegrityError:
        return None  # same sender bound concurrently via another invite; caller resumes
    session.add(
        AgentBinding(
            tenant_id=tenant.id,
            chief_identity=f"chief:{tenant.id}",
            research_identity=f"research:{tenant.id}",
            reviewer_identity=f"reviewer:{tenant.id}",
            vm_ids=None,
            capacity="PENDING (G05/G06)",
        )
    )
    invite.state, invite.consumed_by, invite.consumed_at = "consumed", tenant.id, now
    return tenant


def _only_greeting(text: str | None) -> bool:
    """True for "Hi <code>" and similar: nothing to answer beyond the invite itself."""
    rest = redact(text).replace("[invite code]", " ").strip(" ,.!").upper()
    return rest == "" or rest in GREETINGS


def _route(session: Session, msg: InboundMessage, tenant: Tenant, now: datetime) -> str:
    if msg.provider_time > (tenant.last_inbound_at or msg.provider_time - timedelta(1)):
        tenant.last_inbound_at = msg.provider_time
    release_waiting(session, tenant.id, now)  # window is open again
    if msg.type != "text":
        _queue(session, msg, tenant, "text_only", msgs.TEXT_ONLY, now)
        return "non_text"
    words = (msg.text or "").strip().upper()
    if find_code(msg.text) and _only_greeting(msg.text):
        return "repeat_invite"  # already bound: no second welcome, no reply
    words = {"/STOP": "STOP", "/START": "START"}.get(words, words)  # Telegram commands
    if words == "STOP":
        tenant.opt_in_state = "stopped"
        _queue(session, msg, tenant, "stop", msgs.STOP, now)
        return "stop"
    if words == "START":
        tenant.opt_in_state = "asked"
        _queue(session, msg, tenant, "start", msgs.START, now)
        return "start"
    feedback = parse_feedback(msg.text)
    if feedback is not None:
        record_feedback(session, tenant, msg.message_id, feedback[0], feedback[1], now)
        _queue(session, msg, tenant, "feedback_ack", FEEDBACK_ACK, now)
        return f"feedback_{feedback[0]}"
    if words == "TEXT" or words.startswith("TEXT "):
        return _send_text_version(session, msg, tenant, now, words.removeprefix("TEXT").strip())
    if tenant.opt_in_state == "asked" and words in YES_WORDS:
        tenant.opt_in_state = "yes"
        _queue(session, msg, tenant, "opt_in_yes", msgs.OPT_IN_YES, now)
        return "opt_in_yes"
    if tenant.opt_in_state == "asked" and words in NO_WORDS:
        tenant.opt_in_state = "no"
        _queue(session, msg, tenant, "opt_in_no", msgs.OPT_IN_NO, now)
        return "opt_in_no"
    reason = tenant.pending_reason or msgs.PENDING_REASON
    _queue(
        session, msg, tenant, "question_pending", msgs.QUESTION_PENDING.format(reason=reason), now
    )
    return "question_pending"


def _send_text_version(
    session: Session, msg: InboundMessage, tenant: Tenant, now: datetime, what: str
) -> str:
    """TEXT -> list of sections; TEXT R05 -> one section; TEXT ALL -> the whole report.
    Today's report only: an older report is never offered as today's."""
    latest = session.execute(
        select(StoredReport)
        .where(StoredReport.tenant_id == tenant.id, StoredReport.trading_date == ist_today(now))
        .order_by(StoredReport.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        _queue(session, msg, tenant, "text_none", msgs.NO_REPORT_TODAY, now)
        return "text_none"
    report = Report.model_validate(latest.body)
    if what == "":
        _queue(session, msg, tenant, "text_index", text_index(report), now)
        return "text_index"
    charts = {c.manifest["lens"]: c.alt_text for c in report_charts(report)}
    if what == "ALL":
        for i, body in enumerate(text_parts(report, chart_texts=charts), 1):
            _queue(session, msg, tenant, "text_part", body, now, key=f"{msg.message_id}:text:p{i}")
        return "text_all"
    try:
        lens = LensId(what)
    except ValueError:
        _queue(session, msg, tenant, "text_index", text_index(report), now)
        return "text_index"
    _queue(session, msg, tenant, "text_section", text_section(report, lens, charts), now)
    return f"text_{lens.value}"


def _neutral(session: Session, msg: InboundMessage, now: datetime) -> str:
    recent = session.execute(
        select(
            exists().where(
                Outbox.recipient == msg.sender,
                Outbox.kind == "neutral",
                Outbox.created_at > now - NEUTRAL_WINDOW,
            )
        )
    ).scalar()
    if recent:
        return "not_bound_quiet"  # at most one neutral reply per sender per 24h
    _queue(
        session,
        msg,
        None,
        "neutral",
        msgs.NEUTRAL,
        now,
        key=f"neutral:{msg.channel}:{msg.phone_number_id}:{msg.sender}:{now:%Y-%m-%d}",
    )
    return "not_bound"


def _privacy_notice(session: Session, wa, msg: InboundMessage, tenant: Tenant, now) -> None:
    """GATES.md T1 (DRAFT wording): Telegram only, only with TELEGRAM_PRIVACY_NOTICE=1,
    queued right after the welcome. The key is per tenant, so it is never queued twice."""
    if msg.channel != "telegram" or not getattr(wa, "privacy_notice", False):
        return
    body = msgs.privacy_notice_telegram(encrypted=pii.enabled())
    _queue(session, msg, tenant, "privacy_notice", body, now, key=f"privacy_notice:{tenant.id}")


BLOCKED_REASON = "recipient blocked the bot (my_chat_member)"


def handle_bot_blocked(
    session: Session, bot_id: str, key: str, chat_id: str, provider_time: datetime, now
) -> str:
    """TELEGRAM_TRACK_MEMBER_UPDATES: the person blocked the bot (my_chat_member "kicked").
    Same opt-out as a 403 on send, plus their pending proactive rows are cancelled.
    Deduplicated on `key` in inbound_messages (like messages), so a re-delivered update
    can never undo a later START. The caller owns the transaction."""
    fresh = session.execute(
        pg_insert(Inbound)
        .values(
            message_id=key,
            channel="telegram",
            business_phone_id=bot_id,
            sender=chat_id,
            provider_time=provider_time,
            received_at=now,
            type="my_chat_member",
            handled_as="received",
        )
        .on_conflict_do_nothing(index_elements=["message_id"])
        .returning(Inbound.message_id)
    ).first()
    if fresh is None:
        return "duplicate"
    who = InboundMessage(
        message_id=key,
        waba_id="",
        phone_number_id=bot_id,
        sender=chat_id,
        provider_time=provider_time,
        type="other",
        text=None,
        channel="telegram",
    )
    tenant = _find_tenant(session, who)
    inbound = session.get(Inbound, key)
    if tenant is None:
        inbound.handled_as = "blocked_no_tenant"
        return inbound.handled_as
    opt_out_blocked(tenant)
    cancel_pending_proactive(session, tenant.id, now, BLOCKED_REASON)
    inbound.handled_as, inbound.tenant_id = "blocked", tenant.id
    return "blocked"


def handle_message(
    session: Session,
    wa: WhatsAppSettings | TelegramSettings,
    msg: InboundMessage,
    now: datetime,
) -> Outcome:
    """Process one message. The caller owns the transaction (commit / rollback)."""
    if not wa.accepts(msg):  # our own WhatsApp number / Telegram bot only
        return Outcome("ignored_wrong_business_number")
    fresh = session.execute(
        pg_insert(Inbound)
        .values(
            message_id=msg.message_id,
            channel=msg.channel,
            business_phone_id=msg.phone_number_id,
            sender=msg.sender,
            provider_time=msg.provider_time,
            received_at=now,
            type=msg.type,
            handled_as="received",
        )
        .on_conflict_do_nothing(index_elements=["message_id"])
        .returning(Inbound.message_id)
    ).first()
    if fresh is None:
        return Outcome("duplicate")

    allows = getattr(wa, "allows", None)  # B07: Telegram chat-id allowlist (unset = all)
    if allows is not None and not allows(msg.sender):
        # Not on TELEGRAM_ALLOWED_CHAT_IDS: same neutral reply (and 24h limit) as a bad
        # invite; nothing is bound or consumed, an existing tenant is not routed.
        handled = _neutral(session, msg, now).replace("not_bound", "not_allowed")
        session.get(Inbound, msg.message_id).handled_as = handled
        return Outcome(handled)

    tenant = _find_tenant(session, msg)
    if tenant is not None:
        handled = _route(session, msg, tenant, now)
    else:
        tenant = _try_bind(session, msg, now, bool(getattr(wa, "require_prebind", False)))
        if tenant is not None:
            _queue(
                session,
                msg,
                tenant,
                "welcome",
                msgs.WELCOME_PENDING.format(reason=tenant.pending_reason),
                now,
            )
            _privacy_notice(session, wa, msg, tenant, now)
            _queue(session, msg, tenant, "opt_in", msgs.OPT_IN, now)
            handled = "bound"
        else:
            tenant = _find_tenant(session, msg)  # bound meanwhile by a parallel webhook?
            handled = _route(session, msg, tenant, now) if tenant else _neutral(session, msg, now)

    inbound = session.get(Inbound, msg.message_id)
    inbound.handled_as = handled
    if tenant is not None:  # store text only for bound tenants, invite codes redacted
        body = MessageBody(id=uuid.uuid4(), tenant_id=tenant.id, body_redacted=redact(msg.text))
        session.add(body)
        session.flush()
        inbound.tenant_id, inbound.safe_content_ref = tenant.id, body.id
    return Outcome(handled, tenant.id if tenant else None)
