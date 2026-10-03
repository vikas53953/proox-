"""B05: in the D07 export, a very long chat bubble may split across printed pages, its
label line repeated on each continuation (table header); normal bubbles stay whole."""

import html
import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_html", REPO / "uat" / "render_html.py")
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)

T = "2026-10-01T03:15:00+00:00"


def ev(text: str, direction: str = "out") -> dict:
    return {"dir": direction, "thread": "vikas", "t": T, "text": text, "state": "READ"}


def long_text(paras: int = 6) -> str:
    return "\n\n".join(
        "\n".join(f"R{p:02d} line {i} " + "x" * 60 for i in range(5)) for p in range(paras)
    )


def print_css() -> str:
    return rh.CSS.split("@media print {", 1)[1]


def test_normal_bubble_stays_whole():
    out = rh.bubble(ev("Short reply\n\nSecond paragraph"))
    assert out.startswith('<div class="msg">') and "split" not in out
    assert re.search(r"\.msg \{[^}]*break-inside: avoid", print_css())


def test_very_long_bubble_splits_with_repeated_label(monkeypatch):
    monkeypatch.setattr(rh, "EXPAND", True)  # the standalone export shows full text
    text = long_text()
    assert rh.printed_lines(text) > rh.SPLIT_LINES
    out = rh.bubble(ev(text))
    assert out.startswith('<table class="msg split" role="presentation">')
    head = re.search(r"<thead>(.*?)</thead>", out).group(1)
    assert '<div class="who">Research desk</div>' in head
    bodies = re.findall(r'<div class="body">(.*?)</div>', out, re.S)
    assert len(bodies) == 6  # one row per paragraph: breaks fall between paragraphs
    assert "\n".join(html.unescape(b) for b in bodies) == text  # text kept exactly
    assert '<tr class="end"><td><div class="meta">08:45 IST · read</div>' in out
    css = print_css()
    assert re.search(r"table\.msg\.split \{[^}]*break-inside: auto", css)
    assert re.search(r"table\.msg\.split thead \{ display: table-header-group", css)


def test_collapsed_long_bubble_and_media_are_not_split(monkeypatch):
    monkeypatch.setattr(rh, "EXPAND", False)
    assert "<details>" in rh.bubble(ev(long_text())) and "split" not in rh.bubble(ev(long_text()))
    monkeypatch.setattr(rh, "EXPAND", True)
    media = dict(ev(long_text()), media={"kind": "image", "file": "media/x.png"})
    assert "split" not in rh.bubble(media)


def test_committed_export_uses_the_split_layout():
    page = (REPO / "samples" / "uat" / "D07-mock-uat.html").read_text()
    assert "table.msg.split thead { display: table-header-group; }" in page
    splits = re.findall(r'<table class="msg split"[^>]*>.*?</table>', page, re.S)
    assert splits and any("TEXT R05" in s for s in splits)
    for s in splits:
        assert re.search(r'<thead><tr><td><div class="who">[^<]+</div></td></tr></thead>', s)
    assert page.count('<div class="msg') > 10  # normal bubbles remain plain, unsplit
