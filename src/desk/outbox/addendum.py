"""Optional 09:12 IST indicative-auction addendum (Design: AUCTION template draft).

One short message that names the morning report it updates, lists only INDICATIVE
AUCTION values (with source and time), and says plainly what is still unknown.
Expires at 09:15 IST: after the open it is no longer useful and is cancelled, not sent.
"""

from desk.core.facts import DataClass
from desk.core.lens import LensId
from desk.market_calendar import fmt_ist
from desk.report.model import Report
from desk.report.text import GAP_TOPICS, fact_lines, plain_reason

CAVEAT = (
    "Indicative auction values can change until the pre-open closes; they are not "
    "the opening price."
)


def addendum_text(auction: Report, morning_ref: str | None) -> str:
    r07 = next(r for r in auction.lenses if r.lens is LensId.R07)
    changed = [f for f in r07.facts if f.data_class is DataClass.INDICATIVE_AUCTION]
    unknown = [g for g in r07.gaps if g.degrades]
    mock = "MOCK | " if auction.is_mock else ""
    ref = (
        f"Update to {morning_ref}"
        if morning_ref
        else "Morning report not available today; standalone update"
    )
    lines = [
        f"{mock}{auction.id} v{auction.version} | {auction.trading_date:%d %b %Y} | "
        f"as of {fmt_ist(auction.cutoff)} | INDICATIVE AUCTION",
        ref,
        "",
        "Changed:",
    ]
    # same shared-provenance rule as the report: one source/time line per group
    lines += fact_lines(tuple(changed)) or ["- nothing: no indicative auction values available"]
    lines += ["", "Still unknown:"]
    lines += [
        f"- {GAP_TOPICS.get(g.topic, g.topic)}: {plain_reason(g.topic, g.reason)}" for g in unknown
    ] or ["- final auction price and today's continuous order flow (do not exist yet)"]
    lines += ["", CAVEAT]
    return "\n".join(lines)
