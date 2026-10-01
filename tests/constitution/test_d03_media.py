"""D03 / D04 / D05 generated-media checks with real pixels and real PDF text.

D03: 390 px phone preview, greyscale, opaque background (light/dark chat shells),
     PDF page breaks with header/footer on every page.
D04: units, source, as-of on the media; chart inputs come only from sourced facts;
     renderer manifest records them.
D05: a text version for every attachment; no colour-only meaning; MOCK visible.
"""

import hashlib
import io
import json

import pytest
from PIL import Image

from desk.core.facts import DataClass
from desk.render.charts import (
    PREVIEW_WIDTH_PX,
    WIDTH_PX,
    ChartLayoutError,
    layout_problems,
    report_charts,
)
from desk.render.palette import GREY, INK, NAVY, WHITE, contrast
from desk.render.pdf import FontBlockedError, render_pdf
from tests.media_helpers import pdf_pages_text


@pytest.fixture(scope="module")
def mock_report(build_report):
    return build_report()


@pytest.fixture(scope="module")
def chart(mock_report):
    (c,) = report_charts(mock_report)
    return c


@pytest.fixture(scope="module")
def pdf_pages(mock_report, chart):
    return pdf_pages_text(render_pdf(mock_report, [chart]))


# ---- PNG ----------------------------------------------------------------------------------


def test_png_is_1080_wide_opaque_rgb(chart):
    img = Image.open(io.BytesIO(chart.png))
    assert img.size[0] == WIDTH_PX and img.mode == "RGB"  # no alpha: safe on dark shells
    assert img.getpixel((2, 2)) == (255, 255, 255)


def test_smallest_text_survives_the_390px_preview(chart):
    scale = PREVIEW_WIDTH_PX / WIDTH_PX
    assert chart.manifest["min_text_px"] * scale >= 8.5  # 24 px -> ~8.7 px on the phone
    img = Image.open(io.BytesIO(chart.png))
    preview = img.resize((PREVIEW_WIDTH_PX, round(img.size[1] * scale)), Image.LANCZOS)
    dark = sum(1 for px in preview.convert("L").getdata() if px < 128)
    assert dark > 1500  # bars and text are still there after downscaling


def test_palette_contrast_meets_targets():
    assert contrast(INK, WHITE) >= 4.5 and contrast(GREY, WHITE) >= 4.5  # text
    assert contrast(NAVY, WHITE) >= 3.0  # chart marks


def test_bars_stay_distinct_from_background_in_greyscale(chart):
    grey = Image.open(io.BytesIO(chart.png)).convert("L")
    histogram = grey.histogram()
    bar_level = round(0.299 * 0x24 + 0.587 * 0x4A + 0.114 * 0x63)  # NAVY in greyscale
    bar_pixels = sum(histogram[bar_level - 3 : bar_level + 4])
    assert bar_pixels > 20000  # the bars are a large, solid dark region
    l_bar, l_bg = bar_level / 255, 1.0
    assert (l_bg + 0.05) / (l_bar + 0.05) >= 3.0


def test_chart_text_names_unit_source_asof_and_mock(chart, mock_report):
    texts = " | ".join(chart.figure_texts)
    for must in (
        "% change",
        "Bars start at zero",
        "Source: MOCK feed: sectors",
        "As of 30 Sep 2026 15:30 IST",
        mock_report.id,
        "MOCK FIXTURE",
        "FONT: PLACEHOLDER",
    ):
        assert must in texts, must


def test_meaning_never_depends_on_colour(chart):
    values = [t for t in chart.figure_texts if t.endswith((" up", " down", " flat"))]
    assert len(values) == 4  # every bar has a direct label with sign AND word
    assert all(t[0] in "+−0" for t in values)
    assert chart.manifest["palette"]["marks"] == NAVY  # one colour for up and down


def test_layout_checker_catches_clipping_and_overlap():
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(3, 2), dpi=100)
    fig.text(0.9, 0.5, "this text runs off the right edge", fontsize=14)
    fig.text(0.1, 0.2, "overlap A", fontsize=14)
    fig.text(0.12, 0.21, "overlap B", fontsize=14)
    problems = layout_problems(fig)
    plt.close(fig)
    assert any(p.startswith("clipped") for p in problems)
    assert any(p.startswith("overlap") for p in problems)
    assert issubclass(ChartLayoutError, ValueError)


# ---- D04 manifest / inputs ----------------------------------------------------------------


def test_manifest_records_sourced_inputs_and_hashes(chart, mock_report):
    m = chart.manifest
    assert m["report_id"] == mock_report.id and m["report_hash"] == mock_report.content_hash
    assert m["png_sha256"] == hashlib.sha256(chart.png).hexdigest()
    assert (
        m["inputs_sha256"]
        == hashlib.sha256(json.dumps(m["inputs"], sort_keys=True).encode()).hexdigest()
    )
    for item in m["inputs"]:
        assert item["source_url"].startswith("https://") and item["as_of"]
        assert item["data_class"] == str(DataClass.PRIOR_SESSION)
    assert m["layout_problems"] == []


def test_no_chart_from_stale_inputs(build_report):
    assert report_charts(build_report("prior_session_stale")) == []


def test_every_chart_has_a_text_version(chart):
    for word in ("Auto +1.05% up", "IT −0.35% down", "Source:", "As of"):
        assert word in chart.alt_text


# ---- PDF ----------------------------------------------------------------------------------


def test_pdf_every_page_has_header_footer_and_page_x_of_y(pdf_pages, mock_report):
    n = len(pdf_pages)
    assert n >= 2
    for i, page in enumerate(pdf_pages, 1):
        assert "MOCK | RESEARCH | India pre-market | 01 Oct 2026" in page
        assert f"{mock_report.id} v1" in page and f"page {i} of {n}" in page
        assert "FONT: PLACEHOLDER" in page


def test_pdf_text_is_searchable_and_complete(pdf_pages, mock_report):
    text = "\n".join(pdf_pages)
    for r in mock_report.lenses:
        assert f"{r.lens.value} " in text
    assert "https://example.invalid/mock/sectors" in text  # sources beside claims
    assert "Text version of chart" in text


def test_pdf_shows_missing_values_as_not_zero(build_report):
    report = build_report("no_orderflow_rights")
    text = "\n".join(pdf_pages_text(render_pdf(report, report_charts(report))))
    assert "N/A (not zero)" in text and "no licensed trade-event feed" in text


def test_real_report_pdf_refused_while_font_is_blocked(mock_report):
    real = mock_report.model_copy(update={"is_mock": False})
    with pytest.raises(FontBlockedError):
        render_pdf(real, [])


def test_pdf_and_summary_agree_on_paths_and_gaps(pdf_pages, mock_report):
    """D02: the chat summary and the PDF state the same paths and gaps."""
    from desk.outbox.parts import delivery_plan

    text = "\n".join(pdf_pages).replace("\n", " ")
    summary = delivery_plan(mock_report, mock_report.cutoff, mock_report.cutoff, [], b"x")[0]
    for gap in mock_report.top_gaps:
        assert gap[:60] in summary.body
        assert gap[:60] in text
    for s in mock_report.scenarios:
        assert s.trigger in summary.body
        assert s.trigger[:60] in text
