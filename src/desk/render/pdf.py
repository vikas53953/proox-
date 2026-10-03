"""Full R01-R15 PDF report (ReportLab, A4).

Design doc v1.0: header with report id / version / RESEARCH / as-of on every page;
footer with report id, hash and "page X of Y"; searchable text (no text drawn as images);
tables with a repeating header row; missing values shown as UNAVAILABLE + reason, never
zero; body 11 pt, headings 18 pt, footnotes 9 pt.

Font: Noto Sans is BLOCKED (asset/licence/hash not approved). The owner approved the
built-in Helvetica as a visibly labelled PLACEHOLDER for MOCK reports only, so a non-mock
report is refused here rather than silently printed in an unapproved font.
"""

import io
from collections import Counter
from itertools import groupby
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from desk.market_calendar import fmt_ist
from desk.render.charts import Chart
from desk.render.palette import GREY, INK, NAVY, RULES, WARNING
from desk.report.model import Report
from desk.report.text import (
    GAP_TOPICS,
    compact_paths,
    flat,
    gap_lines,
    plain_reason,
    provenance_key,
)

PDF_VERSION = "pdf-a4-v1"
FONT, FONT_BOLD = "Helvetica", "Helvetica-Bold"
FONT_LABEL = "FONT: PLACEHOLDER (Helvetica; Noto BLOCKED)"
MARGIN = 16 * mm
# Helvetica (WinAnsi) cannot draw these; map them to safe equivalents.
_SAFE = {"−": "-", "→": "->", "·": "|", "≤": "<=", "≥": ">="}


class FontBlockedError(RuntimeError):
    pass


def _t(text: object) -> str:
    s = str(text)
    for bad, good in _SAFE.items():
        s = s.replace(bad, good)
    return escape(s.encode("cp1252", errors="replace").decode("cp1252"))


STYLES = {
    "h1": ParagraphStyle(
        "h1",
        fontName=FONT_BOLD,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor(INK),
        spaceAfter=4,
    ),
    "h2": ParagraphStyle(
        "h2",
        fontName=FONT_BOLD,
        fontSize=13,
        leading=17,
        textColor=colors.HexColor(NAVY),
        spaceBefore=10,
        spaceAfter=4,
    ),
    "body": ParagraphStyle(
        "body",
        fontName=FONT,
        fontSize=11,
        leading=14.5,
        textColor=colors.HexColor(INK),
        alignment=TA_LEFT,
    ),
    "cell": ParagraphStyle(
        "cell", fontName=FONT, fontSize=9.5, leading=12, textColor=colors.HexColor(INK)
    ),
    "head": ParagraphStyle(
        "head", fontName=FONT_BOLD, fontSize=9.5, leading=12, textColor=colors.HexColor(INK)
    ),
    "foot": ParagraphStyle(
        "foot", fontName=FONT, fontSize=9, leading=11.5, textColor=colors.HexColor(GREY)
    ),
    "warn": ParagraphStyle(
        "warn", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=colors.HexColor(WARNING)
    ),
}


def _numbered_canvas(report: Report):
    class NumberedCanvas(rl_canvas.Canvas):
        """Two-pass canvas so every page can say "page X of Y"."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):  # noqa: N802 (ReportLab API name)
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total: int) -> None:
            w, h = A4
            self.setFont(FONT_BOLD, 9)
            self.setFillColor(colors.HexColor(WARNING if report.is_mock else INK))
            mock = "MOCK | " if report.is_mock else ""
            self.drawString(
                MARGIN,
                h - 10 * mm,
                _t(
                    f"{mock}RESEARCH | India pre-market | {report.trading_date:%d %b %Y} | "
                    f"as of {fmt_ist(report.cutoff)} | {report.id} v{report.version}"
                ),
            )
            self.setFont(FONT, 8)
            self.setFillColor(colors.HexColor(GREY))
            self.drawString(
                MARGIN,
                9 * mm,
                _t(
                    f"{report.id} v{report.version} | hash {report.content_hash[:16]} | "
                    f"{FONT_LABEL}"
                ),
            )
            self.drawRightString(w - MARGIN, 9 * mm, f"page {self._pageNumber} of {total}")
            self.setStrokeColor(colors.HexColor(RULES))
            self.line(MARGIN, h - 12 * mm, w - MARGIN, h - 12 * mm)
            self.line(MARGIN, 12 * mm, w - MARGIN, 12 * mm)

    return NumberedCanvas


COLS = (0.27, 0.17, 0.2, 0.36)
HEADERS = ("Topic / instrument", "Value / unit", "Status / as of (IST)", "Source / note")
BASE_STYLE = [
    ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(RULES)),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
]


def _cells(*texts: str, style: str = "cell") -> list:
    return [Paragraph(_t(t), STYLES[style]) for t in texts]


def _table(rows: list, extra: list, repeat: int = 0) -> LongTable:
    width = A4[0] - 2 * MARGIN
    t = LongTable(rows, colWidths=[width * c for c in COLS], repeatRows=repeat, splitByRow=True)
    t.setStyle(TableStyle(BASE_STYLE + extra))
    return t


def _lens_tables(r) -> list:
    """Same rule as chat: consecutive facts sharing source, time and note get ONE
    provenance row. Each such group is its own table whose provenance row repeats at
    the top of a new page, so no value is ever shown without its source and time."""
    if not r.facts and not r.gaps:
        return []
    out = [
        _table(
            [_cells(*HEADERS, style="head")],
            [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(RULES))],
        )
    ]
    singles: list = []

    def flush_singles() -> None:
        if singles:
            out.append(_table(list(singles), []))
            singles.clear()

    for _, run in groupby(r.facts, key=provenance_key):
        group = list(run)
        if len(group) == 1:
            f = group[0]
            singles.append(
                _cells(
                    f"{f.label} — {f.instrument}",
                    f"{f.value} {f.unit}",
                    f"{f.data_class} | {fmt_ist(f.as_of)}",
                    f"{f.source.name} {f.source.url}" + (f" ({f.note})" if f.note else ""),
                )
            )
            continue
        flush_singles()
        first = group[0]
        main = Counter(f.data_class for f in group).most_common(1)[0][0]
        instruments = {f.instrument for f in group}
        common = next(iter(instruments)) if len(instruments) == 1 else None
        head = (
            f"{len(group)} items: {main} unless marked | as of {fmt_ist(first.as_of)} | "
            f"{first.source.name} {first.source.url}"
            + (f" | {common}" if common else "")
            + (f" | {first.note}" if first.note else "")
        )
        rows = [[Paragraph(_t(head), STYLES["head"]), "", "", ""]]
        for f in group:
            topic = (
                f.label
                if common or f.instrument.lower() in f.label.lower()
                else (f"{f.label} — {f.instrument}")
            )
            rows.append(
                _cells(
                    topic,
                    f"{f.value}" + ("" if f.unit == "text" else f" {f.unit}"),
                    "" if f.data_class == main else str(f.data_class),
                    "",
                )
            )
        out.append(
            _table(
                rows,
                [
                    ("SPAN", (0, 0), (-1, 0)),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(RULES)),
                ],
                repeat=1,
            )
        )
    flush_singles()
    gap_rows = [
        _cells(
            GAP_TOPICS.get(g.topic, g.topic),
            "N/A (not zero)",
            str(g.data_class),
            plain_reason(g.topic, g.reason) + (f". Effect: {flat(g.effect)}" if g.effect else ""),
        )
        for g in r.gaps
    ]
    if gap_rows:
        out.append(_table(gap_rows, []))
    return out


def render_pdf(report: Report, charts: list[Chart]) -> bytes:
    if not report.is_mock:
        raise FontBlockedError("real reports need the approved Noto font asset (BLOCKED)")
    s = STYLES
    story = [
        Paragraph(_t(f"India pre-market research | {report.trading_date:%d %b %Y}"), s["h1"]),
        Paragraph(
            _t(
                f"Report {report.id} v{report.version} | as of {fmt_ist(report.cutoff)} "
                f"| status {report.review_status} | RESEARCH only: no orders, no "
                f"simulated account"
            ),
            s["body"],
        ),
    ]
    if report.is_mock:
        story.append(
            Paragraph(
                "MOCK FIXTURE - synthetic values, not market data. " + _t(FONT_LABEL), s["warn"]
            )
        )
    story += [Paragraph("Session paths (conditional, not predictions)", s["h2"])]
    story += [Paragraph(_t(line.removeprefix("- ")), s["body"]) for line in compact_paths(report)]
    for c in dict.fromkeys(c for sc in report.scenarios for c in sc.contrary_evidence):
        story.append(Paragraph(_t(f"Contrary evidence: {c}"), s["body"]))
    story += [Paragraph("Data gaps", s["h2"])]
    story += [Paragraph(_t(g.removeprefix("- ")), s["body"]) for g in gap_lines(report)]
    story.append(
        Paragraph(
            _t(
                "Audio summary: UNAVAILABLE (voice not decided, G04). This PDF "
                "and the chat text are the full record."
            ),
            s["foot"],
        )
    )
    by_lens = {c.manifest["chart"]: c for c in charts}
    for r in report.lenses:
        tables = _lens_tables(r)
        heading = Paragraph(_t(f"{r.lens} {r.title} — {r.status}"), s["h2"])
        # keep the heading with the column header and the first table (or group)
        story.append(KeepTogether([heading, *tables[:2]]))
        story += tables[2:]
        notes = list(r.notes)
        if r.lens.value == "R14":  # paths are shown once, compactly, at the top
            notes = [n for n in notes if not n.startswith(("BASE:", "UP:", "DOWN:"))]
            notes.append("Session paths: see the top of this report.")
        story += [Paragraph(_t(f"Note: {n}"), s["foot"]) for n in notes]
        if r.uncertainty:
            story.append(Paragraph(_t(f"Uncertainty: {r.uncertainty}"), s["foot"]))
        if r.lens.value == "R04" and "sector_returns" in by_lens:
            chart = by_lens["sector_returns"]
            w = A4[0] - 2 * MARGIN
            h = w * chart.manifest["size_px"][1] / chart.manifest["size_px"][0]
            story += [
                Spacer(1, 4),
                KeepTogether(
                    [
                        Image(io.BytesIO(chart.png), width=w * 0.8, height=h * 0.8),
                        Paragraph(_t(f"Text version of chart: {chart.alt_text}"), s["foot"]),
                    ]
                ),
            ]
    methods = ", ".join(f"{k}={v}" for k, v in sorted(report.method_versions.items()))
    story += [
        Spacer(1, 8),
        Paragraph(
            _t(
                f"Feed {report.feed} | model {report.model} | calendar {report.calendar_version} | "
                f"methods {methods} "
                f"| renderer {PDF_VERSION} | full hash {report.content_hash}"
            ),
            s["foot"],
        ),
    ]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{report.id} v{report.version}",
        author="desk (RESEARCH)",
        subject="India pre-market research report",
    )
    doc.build(story, canvasmaker=_numbered_canvas(report))
    return buf.getvalue()
