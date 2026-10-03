"""Plain-text rendering of a report: the accessible fallback for every attachment.

Rendering only — every Fact keeps its own source, time and class in the data model.
To keep chat readable:
- consecutive facts with the same source, time and note share ONE provenance line
  ("17 items: PRIOR SESSION unless marked | as of ... | source"); a fact whose class
  differs is marked inline, a fact with a different source/time keeps its own tag;
- session paths name the most-used instrument once ("Levels on X unless marked") and
  mark any other instrument in brackets;
- data gaps are written as plain sentences, without nested brackets.
"""

import re
from collections import Counter
from itertools import groupby

from desk.core.facts import Fact
from desk.core.lens import LensId, LensResult
from desk.feeds.base import TERMS_GAP_TOPIC
from desk.market_calendar import fmt_ist
from desk.report.model import Report

MOCK_BANNER = "MOCK — synthetic values, not market data"
GAP_TOPICS = {
    "gift_nifty": "GIFT Nifty",
    "trades": "Trade data (order flow)",
    "pre_open": "Pre-open data",
    "option_chain": "Option chain",
    "fno": "F&O data",
    "fut_bars": "Futures bars",
    "index_bars": "Index bars",
    "margin financing": "Margin financing",
    TERMS_GAP_TOPIC: "Data usage terms",
}
GROUP_HEAD = re.compile(r"^(\d+) items: ")  # shared-provenance group header (see _pack)
_PAREN = re.compile(r"\s*\(([^()]*)\)")


def flat(text: str) -> str:
    """'a (b (c))' -> 'a, b, c': one level of meaning, no nested brackets."""
    prev = None
    while prev != text:
        prev, text = text, _PAREN.sub(r", \1", text)
    return text.strip(" ,")


def _fact_line(f: Fact) -> str:
    line = (
        f"- {f.label}: {f.value} {f.unit} [{f.data_class}] — {f.instrument}; "
        f"{f.source.name}, as of {fmt_ist(f.as_of)}"
    )
    return line + (f" ({f.note})" if f.note else "")


def provenance_key(f: Fact) -> tuple:
    return (f.source.name, f.source.url, f.as_of, f.note)


def fact_lines(facts: tuple[Fact, ...]) -> list[str]:
    out: list[str] = []
    for _, run in groupby(facts, key=provenance_key):
        group = list(run)
        if len(group) == 1:
            out.append(_fact_line(group[0]))
            continue
        first = group[0]
        main = Counter(f.data_class for f in group).most_common(1)[0][0]
        instruments = {f.instrument for f in group}
        common = instruments.pop() if len(instruments) == 1 else None
        head = (
            f"{len(group)} items: {main} unless marked | as of {fmt_ist(first.as_of)} | "
            f"{first.source.name}"
        )
        head += f" | {common}" if common else ""
        head += f" | {first.note}" if first.note else ""
        out.append(head)
        for f in group:
            line = f"- {f.label}: {f.value}" + ("" if f.unit == "text" else f" {f.unit}")
            if f.data_class != main:
                line += f" [{f.data_class}]"
            if common is None and f.instrument.lower() not in f.label.lower():
                line += f" — {f.instrument}"
            out.append(line)
    return out


def _path_instruments(report: Report) -> list[str]:
    r14 = next(r for r in report.lenses if r.lens is LensId.R14)
    return sorted({f.instrument for f in r14.facts}, key=len, reverse=True)


def compact_paths(report: Report) -> list[str]:
    """Paths with the most-used instrument named once; others marked in brackets."""
    instruments = _path_instruments(report)
    texts = [t for s in report.scenarios for t in (s.trigger, s.invalidation)]
    counts = {i: sum(t.count(f" on {i}") for t in texts) for i in instruments}
    default = max(counts, key=counts.get) if counts and max(counts.values()) else None

    def shorten(t: str) -> str:
        for inst in instruments:
            t = t.replace(f" on {inst}", "" if inst == default else f" [{inst}]")
        return t

    out = [f"Levels on {default} unless marked." if default else ""]
    for s in report.scenarios:
        out.append(
            f"- {s.path}: {shorten(s.trigger)}. Invalid if: "
            f"{shorten(s.invalidation)[:1].lower()}{shorten(s.invalidation)[1:]}. "
            f"Confidence: {s.confidence}."
        )
    return [line for line in out if line]


RIGHTS_PREFIX = re.compile(r"^not available under current feed rights \((.*)\)$", re.S)
GATE = re.compile(r"\((G0\d)\)")


def plain_reason(topic: str, reason: str) -> str:
    """'gift_nifty: not available under current feed rights (GIFT Nifty not licensed in
    current feed (G03))' -> 'Not licensed in current feed, gate G03'."""
    reason = reason.removeprefix(f"{topic}: ")
    m = RIGHTS_PREFIX.match(reason)
    if m:
        reason = m.group(1)
    reason = flat(GATE.sub(r"(gate \1)", reason))
    name = GAP_TOPICS.get(topic, topic)
    if reason.lower().startswith(name.lower() + " "):
        reason = reason[len(name) + 1 :]
    return reason[:1].upper() + reason[1:]


def gap_lines(report: Report) -> list[str]:
    """One plain sentence per distinct gap, naming every lens it affects. The per-lens
    effect stays in each lens section, so the summary line is not repeated per lens."""
    grouped: dict[tuple, list[str]] = {}
    for r in report.lenses:
        for g in r.gaps:
            if r.lens is LensId.R15 and g.topic != TERMS_GAP_TOPIC:
                continue  # R15 restates the same gaps per dataset; only its terms gap is new
            if not g.degrades:
                continue
            topic = GAP_TOPICS.get(g.topic, g.topic[:1].upper() + g.topic[1:])
            key = (topic, str(g.data_class).lower(), plain_reason(g.topic, g.reason))
            grouped.setdefault(key, []).append(r.lens.value)
    return [
        f"- {topic}: {cls}. {reason}. Affects {', '.join(dict.fromkeys(ids))}."
        for (topic, cls, reason), ids in grouped.items()
    ] or ["- none"]


def header_line(report: Report) -> str:
    return (
        f"RESEARCH | India pre-market | {report.trading_date:%d %b %Y} | "
        f"As of {fmt_ist(report.cutoff)} | Report {report.id} v{report.version}"
    )


def summary_lines(report: Report) -> list[str]:
    out = [f"Status: {report.review_status}", "", "SESSION PATHS (conditional, not predictions):"]
    out += compact_paths(report)
    contrary = dict.fromkeys(c for s in report.scenarios for c in s.contrary_evidence)
    out += [f"- Contrary evidence: {c}" for c in contrary]
    out += ["", "DATA GAPS:"] + gap_lines(report)
    return out


def lens_lines(r: LensResult, report: Report | None = None) -> list[str]:
    out = [f"{r.lens} {r.title} — {r.status}"]
    out += fact_lines(r.facts)
    out += [
        f"- GAP {GAP_TOPICS.get(g.topic, g.topic)}: {g.data_class} — "
        f"{plain_reason(g.topic, g.reason)}" + (f". Effect: {flat(g.effect)}" if g.effect else "")
        for g in r.gaps
    ]
    notes = list(r.notes)
    if r.lens is LensId.R14 and report is not None:  # paths: same compact form as summary
        notes = [n for n in notes if not n.startswith(("BASE:", "UP:", "DOWN:"))]
        out += compact_paths(report)
    out += [f"- Note: {n}" for n in notes]
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
        out += [""] + lens_lines(r, report)
    out += ["", footer_line(report)]
    return "\n".join(out)
