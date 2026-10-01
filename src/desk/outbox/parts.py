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

from desk.market_calendar import fmt_ist
from desk.render.charts import Chart
from desk.report.model import Report
from desk.report.text import MOCK_BANNER, footer_line, lens_lines, summary_lines
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
    parts, current = [], ""
    for block in blocks:
        for line in block:  # a single huge lens is split line by line
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > budget and current:
                parts.append(current)
                candidate = line
            current = candidate
        current += "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


def text_parts(report: Report, late: str = "") -> list[str]:
    """The whole report as numbered text: summary first, then R01-R15."""
    budget = MAX_PART_CHARS - 200
    first = ([MOCK_BANNER] if report.is_mock else []) + summary_lines(report) + [AUDIO_LINE]
    bodies = _pack([first], budget)[:1]
    bodies += _pack([lens_lines(r) for r in report.lenses] + [[footer_line(report)]], budget)
    n = len(bodies)
    bodies[0] += f"\n\nFull R01-R15 report follows in parts 2-{n}."
    return [f"{label(report, i, n, late)}\n{b}" for i, b in enumerate(bodies, 1)]


def delivery_plan(
    report: Report, ready_at: datetime, deadline: datetime, charts: list[Chart], pdf: bytes | None
) -> list[Part]:
    late = late_note(ready_at, deadline)
    if pdf is None:
        return [Part(t) for t in text_parts(report, late)]
    n = 2 + len(charts)
    summary = (
        ([MOCK_BANNER] if report.is_mock else [])
        + summary_lines(report)
        + [AUDIO_LINE, "", "Full R01-R15 report: part 2 (PDF). Text version: reply TEXT."]
    )
    parts = [Part(f"{label(report, 1, n, late)}\n" + "\n".join(summary))]
    stem = f"{report.id}-v{report.version}"
    parts.append(
        Part(
            f"{label(report, 2, n, late)}\nFull R01-R15 report (PDF). Text version: reply TEXT.",
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
        caption = f"{label(report, i, n, late)}\n{chart.alt_text}"
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
