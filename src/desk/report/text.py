"""Plain-text rendering of a report: the accessible fallback for every attachment.

`summary_lines` is the first WhatsApp part (session paths + gaps first); `lens_lines`
renders one lens; `render_text` is the whole report in one piece.
"""

from desk.core.facts import Fact
from desk.core.lens import LensResult
from desk.market_calendar import fmt_ist
from desk.report.model import Report

MOCK_BANNER = "MOCK — synthetic values, not market data"


def _fact_line(f: Fact) -> str:
    line = (
        f"- {f.label}: {f.value} {f.unit} [{f.data_class}] — {f.instrument}; "
        f"{f.source.name}, as of {fmt_ist(f.as_of)}"
    )
    return line + (f" ({f.note})" if f.note else "")


def header_line(report: Report) -> str:
    return (
        f"RESEARCH | India pre-market | {report.trading_date:%d %b %Y} | "
        f"As of {fmt_ist(report.cutoff)} | Report {report.id} v{report.version}"
    )


def summary_lines(report: Report) -> list[str]:
    out = [f"Status: {report.review_status}", "", "SESSION PATHS (conditional, not predictions):"]
    for s in report.scenarios:
        out.append(
            f"- {s.path}: {s.trigger}. Invalidation: {s.invalidation}. Confidence: {s.confidence}."
        )
    contrary = dict.fromkeys(c for s in report.scenarios for c in s.contrary_evidence)
    out += [f"- Contrary evidence: {c}" for c in contrary]
    out += ["", "DATA GAPS:"] + ([f"- {g}" for g in report.top_gaps] or ["- none"])
    return out


def lens_lines(r: LensResult) -> list[str]:
    out = [f"{r.lens} {r.title} — {r.status}"]
    out += [_fact_line(f) for f in r.facts]
    out += [f"- GAP {g.topic}: {g.data_class} — {g.reason}" for g in r.gaps]
    out += [f"- Note: {n}" for n in r.notes]
    if r.uncertainty:
        out.append(f"- Uncertainty: {r.uncertainty}")
    return out


def footer_line(report: Report) -> str:
    return (
        f"Hash {report.content_hash[:16]} | feed {report.feed} | model {report.model} "
        f"| calendar {report.calendar_version}"
    )


def render_text(report: Report) -> str:
    out = ([MOCK_BANNER] if report.is_mock else []) + [header_line(report)]
    out += summary_lines(report)
    for r in report.lenses:
        out += [""] + lens_lines(r)
    out += ["", footer_line(report)]
    return "\n".join(out)
