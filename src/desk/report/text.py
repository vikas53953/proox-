"""Plain-text rendering of a report: the accessible fallback for every attachment.
(M4 splits this into numbered WhatsApp parts and adds PDF/PNG.)"""

from desk.core.facts import Fact
from desk.market_calendar import fmt_ist
from desk.report.model import Report


def _fact_line(f: Fact) -> str:
    line = (
        f"- {f.label}: {f.value} {f.unit} [{f.data_class}] — {f.instrument}; "
        f"{f.source.name}, as of {fmt_ist(f.as_of)}"
    )
    return line + (f" ({f.note})" if f.note else "")


def render_text(report: Report) -> str:
    mock = "MOCK — synthetic values, not market data\n" if report.is_mock else ""
    out = [
        f"{mock}RESEARCH | India pre-market | {report.trading_date:%d %b %Y}",
        f"As of {fmt_ist(report.cutoff)} | Report {report.id} v{report.version} | "
        f"{report.review_status}",
        "",
        "SESSION PATHS (conditional, not predictions):",
    ]
    for s in report.scenarios:
        out.append(
            f"- {s.path}: {s.trigger}. Invalidation: {s.invalidation}. Confidence: {s.confidence}."
        )
    contrary = dict.fromkeys(c for s in report.scenarios for c in s.contrary_evidence)
    out += [f"- Contrary evidence: {c}" for c in contrary]
    out += ["", "DATA GAPS:"] + ([f"- {g}" for g in report.top_gaps] or ["- none"])
    for r in report.lenses:
        out += ["", f"{r.lens} {r.title} — {r.status}"]
        out += [_fact_line(f) for f in r.facts]
        out += [f"- GAP {g.topic}: {g.data_class} — {g.reason}" for g in r.gaps]
        out += [f"- Note: {n}" for n in r.notes]
        if r.uncertainty:
            out.append(f"- Uncertainty: {r.uncertainty}")
    out += [
        "",
        f"Hash {report.content_hash[:16]} | feed {report.feed} | model {report.model} "
        f"| calendar {report.calendar_version}",
    ]
    return "\n".join(out)
