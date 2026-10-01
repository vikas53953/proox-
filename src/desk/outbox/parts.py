"""Split a report into numbered WhatsApp text parts.

Messages can arrive out of order, so EVERY part repeats report id, version, date, as-of
and "part i/n". Part 1 is the short summary with the essential gaps; the full R01-R15
follows. Text bodies stay well under WhatsApp's 4096-character text limit.
"""

from datetime import datetime

from desk.market_calendar import fmt_ist
from desk.report.model import Report
from desk.report.text import MOCK_BANNER, footer_line, lens_lines, summary_lines

MAX_PART_CHARS = 3500


def _label(report: Report, i: int, n: int, late: str) -> str:
    mock = "MOCK | " if report.is_mock else ""
    return (
        f"{mock}{report.id} v{report.version} | {report.trading_date:%d %b %Y} | "
        f"as of {fmt_ist(report.cutoff)} | part {i}/{n}{late}"
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


def report_parts(report: Report, ready_at: datetime, deadline: datetime) -> list[str]:
    late = (
        f" | LATE: ready {fmt_ist(ready_at)}, target {fmt_ist(deadline)}"
        if ready_at > deadline
        else ""
    )
    first = ([MOCK_BANNER] if report.is_mock else []) + summary_lines(report)
    budget = MAX_PART_CHARS - 200  # room for the label line
    bodies = [_pack([first], budget)[0]]
    bodies += _pack([lens_lines(r) for r in report.lenses] + [[footer_line(report)]], budget)
    n = len(bodies)
    bodies[0] += f"\n\nFull R01-R15 report follows in parts 2-{n}."
    return [f"{_label(report, i, n, late)}\n{body}" for i, body in enumerate(bodies, 1)]
