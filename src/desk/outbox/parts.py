"""Turn a report into numbered WhatsApp parts.

Messages can arrive out of order, so EVERY part (text or media caption) starts with
report id, version, date, as-of and "part i/n" (Design J05, D06).

Morning delivery: 1 = short summary with the essential gaps first; 2 = full R01-R15 PDF;
3.. = charts, each captioned with its text version. If no PDF can be made (e.g. a real
report while the font is BLOCKED), the full report goes out as text parts instead —
the text is always the accessible fallback. Text stays under WhatsApp's 4096-char limit;
captions under 1024.
"""

from dataclasses import dataclass
from datetime import datetime

from desk.core.lens import LensId
from desk.market_calendar import fmt_ist
from desk.render.charts import Chart
from desk.report.model import Report
from desk.report.text import GROUP_HEAD, MOCK_BANNER, footer_line, lens_lines, summary_lines
from desk.transport.whatsapp.client import CAPTION_MAX

MAX_PART_CHARS = 3500
AUDIO_LINE = "Audio summary: UNAVAILABLE (voice not decided, G04)."


@dataclass(frozen=True)
class MediaSpec:
    kind: str  # "document" | "image"
    mime: str
    filename: str
    content: bytes
    manifest: dict


@dataclass(frozen=True)
class Part:
    body: str  # full text, or the media caption
    media: MediaSpec | None = None


def label(report: Report, i: int, n: int, late: str = "") -> str:
    mock = "MOCK | " if report.is_mock else ""
    return (
        f"{mock}{report.id} v{report.version} | {report.trading_date:%d %b %Y} | "
        f"as of {fmt_ist(report.cutoff)} | part {i}/{n}{late}"
    )


def late_note(ready_at: datetime, deadline: datetime) -> str:
    return (
        f" | LATE: ready {fmt_ist(ready_at)}, target {fmt_ist(deadline)}"
        if ready_at > deadline
        else ""
    )


def _pack(blocks: list[list[str]], budget: int) -> list[str]:
    """Pack lens blocks into parts. If a block is split, the new part starts with the
    lens title "(continued)" and, inside a shared-provenance group, the group's header
    again — a part read on its own never shows a value without its source and time."""
    parts, current = [], ""
    for block in blocks:
        title, header, remaining = block[0], None, 0
        for line in block:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > budget and current:
                parts.append(current)
                carry = [f"{title} (continued)"]
                if header and remaining > 0:
                    carry.append(f"{header} (continued)")
                candidate = "\n".join([*carry, line])
            current = candidate
            m = GROUP_HEAD.match(line)
            if m:
                header, remaining = line, int(m.group(1))
            elif header and remaining > 0 and line.startswith("- "):
                remaining -= 1
        current += "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


def section_lines(report: Report, lens: LensId, chart_texts: dict[str, str]) -> list[str]:
    """One lens as text; a lens that has a chart also gets the chart's text version."""
    r = next(x for x in report.lenses if x.lens is lens)
    lines = lens_lines(r, report)
    if lens.value in chart_texts:
        lines.append(f"- Chart in words: {chart_texts[lens.value]}")
    return lines


def text_index(report: Report) -> str:
    """Reply to TEXT: what is available, so the customer pulls one section, not nine."""
    mock = "MOCK | " if report.is_mock else ""
    lines = [
        f"{mock}{report.id} v{report.version} | {report.trading_date:%d %b %Y} | "
        f"as of {fmt_ist(report.cutoff)} | TEXT index",
        "Reply TEXT R05 (any section) for one section, or TEXT ALL for everything.",
        "",
    ]
    lines += [f"{r.lens} {r.title} — {r.status}" for r in report.lenses]
    return "\n".join(lines)


def text_section(report: Report, lens: LensId, chart_texts: dict[str, str]) -> str:
    mock = "MOCK | " if report.is_mock else ""
    head = (
        f"{mock}{report.id} v{report.version} | {report.trading_date:%d %b %Y} | "
        f"as of {fmt_ist(report.cutoff)} | TEXT {lens.value}"
    )
    body = "\n".join(section_lines(report, lens, chart_texts))
    return f"{head}\n{body}"[:MAX_PART_CHARS]


def text_parts(
    report: Report, late: str = "", chart_texts: dict[str, str] | None = None
) -> list[str]:
    """The whole report as numbered text (TEXT ALL, or when no PDF can be made)."""
    chart_texts = chart_texts or {}
    budget = MAX_PART_CHARS - 200
    first = ([MOCK_BANNER] if report.is_mock else []) + summary_lines(report) + [AUDIO_LINE]
    bodies = _pack([first], budget)[:1]
    bodies += _pack(
        [section_lines(report, r.lens, chart_texts) for r in report.lenses]
        + [[footer_line(report)]],
        budget,
    )
    n = len(bodies)
    bodies[0] += f"\n\nFull R01-R15 report follows in parts 2-{n}."
    return [f"{label(report, i, n, late)}\n{b}" for i, b in enumerate(bodies, 1)]


def delivery_plan(
    report: Report, ready_at: datetime, deadline: datetime, charts: list[Chart], pdf: bytes | None
) -> list[Part]:
    late = late_note(ready_at, deadline)
    if pdf is None:
        texts = {c.manifest["lens"]: c.alt_text for c in charts}
        return [Part(t) for t in text_parts(report, late, texts)]
    n = 2 + len(charts)
    summary = (
        ([MOCK_BANNER] if report.is_mock else [])
        + summary_lines(report)
        + [
            AUDIO_LINE,
            "",
            "Full R01-R15 report: part 2 (PDF). Text in chat: reply TEXT for the list of sections.",
        ]
    )
    parts = [Part(f"{label(report, 1, n, late)}\n" + "\n".join(summary))]
    stem = f"{report.id}-v{report.version}"
    parts.append(
        Part(
            f"{label(report, 2, n, late)}\nFull R01-R15 report (PDF). Text in chat: reply TEXT.",
            MediaSpec(
                "document",
                "application/pdf",
                f"{stem}.pdf",
                pdf,
                {"renderer": "pdf", "report_hash": report.content_hash},
            ),
        )
    )
    for i, chart in enumerate(charts, 3):
        # The chart's text version lives in the TEXT flow (TEXT R04), not in the caption.
        caption = (
            f"{label(report, i, n, late)}\n{chart.manifest['title']} (chart). "
            f"Text version: reply TEXT {chart.manifest['lens']}."
        )
        parts.append(
            Part(
                caption[:CAPTION_MAX],
                MediaSpec(
                    "image", "image/png", f"{stem}-{chart.name}.png", chart.png, chart.manifest
                ),
            )
        )
    return parts


def report_parts(report: Report, ready_at: datetime, deadline: datetime) -> list[str]:
    """Plain-text plan (kept for callers that need text only)."""
    return text_parts(report, late_note(ready_at, deadline))
