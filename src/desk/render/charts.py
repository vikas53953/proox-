"""PNG charts from report facts only — every value on a chart is a sourced Fact (D04).

Grammar (Design doc v1.0, S87-S90): horizontal bars for sector comparison, bars start at
zero, one axis, direct value labels with sign AND word, title + unit + period + timezone
+ source + as-of on the image, visible MOCK label for synthetic data, a text summary
(alt text) for every chart. No chart is drawn from stale inputs.

Size: 1080 px wide portrait; heading ~52 px, body 32 px, footnotes >= 24 px, so the
smallest text is still ~8.7 px in a 390 px WhatsApp preview.
"""

import hashlib
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from desk.core.facts import DataClass, Fact  # noqa: E402
from desk.core.lens import LensId  # noqa: E402
from desk.market_calendar import fmt_ist  # noqa: E402
from desk.render.palette import GREY, INK, NAVY, RULES, WARNING, WHITE  # noqa: E402
from desk.report.model import Report  # noqa: E402

CHART_VERSION = "chart-hbar-v1"
DPI = 100
WIDTH_PX = 1080
HEADING_PX, BODY_PX, FOOTNOTE_PX = 52, 32, 24
PREVIEW_WIDTH_PX = 390
FONT_NOTE = "FONT: PLACEHOLDER (Matplotlib DejaVu Sans; Noto BLOCKED)"


def _pt(px: int) -> float:
    return px * 72 / DPI


@dataclass(frozen=True)
class Chart:
    name: str
    png: bytes
    alt_text: str
    manifest: dict
    figure_texts: list[str] = field(default_factory=list)


class ChartLayoutError(ValueError):
    """Text clipped by the image edge or two labels overlapping — never ship it."""


def layout_problems(fig) -> list[str]:
    """Measure every rendered text box: inside the image, and no two overlapping."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width, height = fig.bbox.width, fig.bbox.height
    boxes = []
    for t in fig.findobj(lambda a: hasattr(a, "get_text") and hasattr(a, "get_window_extent")):
        if not t.get_visible() or not t.get_text().strip():
            continue
        bb = t.get_window_extent(renderer)
        boxes.append((t.get_text(), bb))
    problems = [
        f"clipped: {s!r}"
        for s, bb in boxes
        if bb.x0 < 0 or bb.y0 < 0 or bb.x1 > width or bb.y1 > height
    ]
    for i, (s1, b1) in enumerate(boxes):
        for s2, b2 in boxes[i + 1 :]:
            if b1.overlaps(b2):
                problems.append(f"overlap: {s1!r} / {s2!r}")
    return problems


def _opaque_rgb(png: bytes) -> bytes:
    """Flatten onto white: no transparency, so dark-mode chat shells can't bleed through."""
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    flat = Image.new("RGB", img.size, WHITE)
    flat.paste(img, mask=img.getchannel("A") if "A" in img.getbands() else None)
    out = io.BytesIO()
    flat.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _signed(v: Decimal, unit: str) -> str:
    if v > 0:
        return f"+{v}{unit} up"
    if v < 0:
        return f"−{abs(v)}{unit} down"
    return f"0{unit} flat"


def sector_returns_chart(report: Report) -> Chart | None:
    r04 = next(r for r in report.lenses if r.lens is LensId.R04)
    facts = [f for f in r04.facts if f.label.endswith(" return") and f.unit == "%"]
    if len(facts) < 2 or any(f.data_class is not DataClass.PRIOR_SESSION for f in facts):
        return None  # missing or stale inputs: no chart rather than a misleading one
    facts.sort(key=lambda f: f.value)
    session = facts[0].as_of
    return _hbar(
        report,
        "sector_returns",
        facts,
        title="Sector returns, prior session",
        subtitle=f"% change of Nifty sector indices, {session:%d %b %Y}. Bars start at zero.",
        names=[f.label.removesuffix(" return") for f in facts],
        unit="%",
    )


def _hbar(
    report: Report,
    name: str,
    facts: list[Fact],
    *,
    title: str,
    subtitle: str,
    names: list[str],
    unit: str,
) -> Chart:
    values = [Decimal(f.value) for f in facts]
    mock = "MOCK · " if report.is_mock else ""
    sources = sorted({(f.source.name, f.source.url) for f in facts})
    as_of = max(f.as_of for f in facts)
    rows = len(facts)
    height_px = 560 + rows * 120
    fig = plt.figure(figsize=(WIDTH_PX / DPI, height_px / DPI), dpi=DPI, facecolor=WHITE)
    texts: list[str] = []

    def text(x, y, s, size_px, color=INK, weight="normal", **kw):
        texts.append(s)
        fig.text(
            x, y, s, fontsize=_pt(size_px), color=color, weight=weight, family="DejaVu Sans", **kw
        )

    top = 1 - 40 / height_px
    text(0.05, top, title, HEADING_PX, weight="bold", va="top")
    text(0.05, top - 80 / height_px, subtitle, FOOTNOTE_PX, GREY, va="top")
    if mock:
        text(
            0.05,
            top - 125 / height_px,
            "MOCK FIXTURE \u2014 synthetic values, not market data",
            FOOTNOTE_PX,
            WARNING,
            weight="bold",
            va="top",
        )
    ax = fig.add_axes((0.25, 260 / height_px, 0.70, (rows * 120) / height_px))
    ax.set_facecolor(WHITE)
    ys = list(range(rows))
    ax.barh(ys, [float(v) for v in values], height=0.55, color=NAVY, zorder=3)
    span = max(abs(float(v)) for v in values) or 1.0
    ax.set_xlim(
        min(0.0, min(float(v) for v in values)) - span * 0.05,
        max(0.0, max(float(v) for v in values)) + span * 0.75,
    )
    ax.axvline(0, color=INK, linewidth=2, zorder=4)  # zero baseline
    ax.set_yticks(ys, names, fontsize=_pt(BODY_PX), color=INK)
    ax.tick_params(axis="y", length=0, pad=12)
    ax.set_xticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="x", color=RULES, zorder=0)
    for y, v in zip(ys, values, strict=True):
        label = _signed(v, unit)
        texts.append(label)
        # Every value label sits right of max(bar end, zero): never on top of category names.
        ax.text(
            max(float(v), 0.0) + span * 0.04,
            y,
            label,
            va="center",
            ha="left",
            fontsize=_pt(BODY_PX),
            color=INK,
            zorder=5,
        )
    src = "; ".join(f"{n} ({u})" for n, u in sources)
    text(0.05, 150 / height_px, f"Source: {src}", FOOTNOTE_PX, GREY, wrap=True)
    text(
        0.05,
        105 / height_px,
        f"As of {fmt_ist(as_of)} · Report {report.id} v{report.version}",
        FOOTNOTE_PX,
        GREY,
    )
    text(0.05, 60 / height_px, f"RESEARCH · {FONT_NOTE}", FOOTNOTE_PX, GREY)
    layout = layout_problems(fig)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor=WHITE)
    plt.close(fig)
    png = _opaque_rgb(buf.getvalue())

    pairs = ", ".join(f"{n} {_signed(v, unit)}" for n, v in zip(names, values, strict=True))
    alt = (
        f"Chart{' (MOCK)' if report.is_mock else ''}: {title}, {unit}. {pairs}. "
        f"Source: {src}. As of {fmt_ist(as_of)}."
    )
    inputs = [
        {
            "label": f.label,
            "value": str(f.value),
            "unit": f.unit,
            "instrument": f.instrument,
            "source_id": f.source.id,
            "source_url": f.source.url,
            "as_of": f.as_of.isoformat(),
            "data_class": str(f.data_class),
        }
        for f in facts
    ]
    manifest = {
        "chart": name,
        "chart_version": CHART_VERSION,
        "renderer": f"matplotlib {matplotlib.__version__}",
        "font": FONT_NOTE,
        "report_id": report.id,
        "report_version": report.version,
        "report_hash": report.content_hash,
        "inputs": inputs,
        "inputs_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
        "png_sha256": hashlib.sha256(png).hexdigest(),
        "size_px": [WIDTH_PX, height_px],
        "min_text_px": FOOTNOTE_PX,
        "palette": {"surface": WHITE, "marks": NAVY, "text": INK, "secondary": GREY},
        "is_mock": report.is_mock,
        "alt_text": alt,
        "layout_problems": layout,
    }
    if layout:
        raise ChartLayoutError(f"{name}: {layout}")
    return Chart(name, png, alt, manifest, texts)


def report_charts(report: Report) -> list[Chart]:
    return [c for c in (sector_returns_chart(report),) if c is not None]
