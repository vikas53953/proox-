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


def _lens_table(r) -> LongTable | None:
    rows = [
        [
            Paragraph(h, STYLES["head"])
            for h in ("Topic / instrument", "Value / unit", "Status / as of (IST)", "Source / note")
        ]
    ]
    for f in r.facts:
        rows.append(
            [
                Paragraph(_t(f"{f.label} — {f.instrument}"), STYLES["cell"]),
                Paragraph(_t(f"{f.value} {f.unit}"), STYLES["cell"]),
                Paragraph(_t(f"{f.data_class} | {fmt_ist(f.as_of)}"), STYLES["cell"]),
                Paragraph(
                    _t(f"{f.source.name} {f.source.url}" + (f" ({f.note})" if f.note else "")),
                    STYLES["cell"],
                ),
            ]
        )
    for g in r.gaps:
        rows.append(
            [
                Paragraph(_t(g.topic), STYLES["cell"]),
                Paragraph("N/A (not zero)", STYLES["cell"]),
                Paragraph(_t(str(g.data_class)), STYLES["cell"]),
                Paragraph(
                    _t(g.reason + (f" Effect: {g.effect}" if g.effect else "")), STYLES["cell"]
                ),
            ]
        )
    if len(rows) == 1:
        return None
    width = A4[0] - 2 * MARGIN
    table = LongTable(
        rows,
        colWidths=[width * 0.27, width * 0.17, width * 0.2, width * 0.36],
        repeatRows=1,
        splitByRow=True,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(RULES)),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(RULES)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


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
    for sc in report.scenarios:
        story.append(
            Paragraph(
                _t(
                    f"{sc.path}: {sc.trigger}. Invalidation: {sc.invalidation}. "
                    f"Confidence: {sc.confidence}."
                ),
                s["body"],
            )
        )
    for c in dict.fromkeys(c for sc in report.scenarios for c in sc.contrary_evidence):
        story.append(Paragraph(_t(f"Contrary evidence: {c}"), s["body"]))
    story += [Paragraph("Data gaps", s["h2"])]
    story += [Paragraph(_t(g), s["body"]) for g in report.top_gaps] or [
        Paragraph("None.", s["body"])
    ]
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
        block = [Paragraph(_t(f"{r.lens} {r.title} — {r.status}"), s["h2"])]
        table = _lens_table(r)
        story.append(KeepTogether(block + ([table] if table and len(r.facts) < 6 else [])))
        if table and len(r.facts) >= 6:
            story.append(table)
        story += [Paragraph(_t(f"Note: {n}"), s["foot"]) for n in r.notes]
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
