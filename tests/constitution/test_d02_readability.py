"""D02 / D06 readability of chat text (rendering only — the data model keeps every
fact's own source, time and class). Guards the owner's UAT findings:
shared provenance printed once, instrument named once, flat gap sentences, and a
part read on its own never shows a value without its source and time."""

import re

import pytest

from desk.core.lens import LensId
from desk.outbox.parts import text_parts
from desk.report.text import GROUP_HEAD, compact_paths, fact_lines, gap_lines


@pytest.fixture(scope="module")
def report(build_report):
    return build_report()


def test_shared_provenance_is_printed_once(report):
    r04 = next(r for r in report.lenses if r.lens is LensId.R04)
    lines = fact_lines(r04.facts)
    assert lines[0].startswith("16 items: PRIOR SESSION unless marked")
    assert "MOCK feed: sectors" in lines[0] and "30 Sep 2026 15:30 IST" in lines[0]
    assert sum("MOCK feed: sectors" in line for line in lines) == 1
    assert sum("[DERIVED]" in line for line in lines) == 4  # differing class still marked


def test_facts_with_their_own_source_keep_their_own_tag(report):
    r01 = next(r for r in report.lenses if r.lens is LensId.R01)
    lines = fact_lines(r01.facts)
    assert not any(GROUP_HEAD.match(line) for line in lines)
    for tag in ("[FACT]", "[RUMOR]", "[INTERPRETATION]"):  # credibility tags unchanged
        assert any(tag in line for line in lines)
    assert all("credibility" in line and "as of" in line for line in lines)


def test_every_value_in_every_part_has_provenance_in_that_part(report):
    """Parts can arrive alone or out of order: a grouped value must have its group
    header inside the same part."""
    for part in text_parts(report):
        remaining = 0
        for line in part.splitlines():
            m = GROUP_HEAD.match(line)
            if m:
                remaining = int(m.group(1))
                continue
            if remaining and line.startswith("- "):
                remaining -= 1
                continue
            remaining = 0
            if line.startswith("- ") and re.search(r": -?\d", line) and "Note" not in line:
                assert "as of" in line or line.startswith(
                    ("- BASE", "- UP", "- DOWN", "- GAP", "- Uncertainty")
                ), line


def test_paths_name_the_main_instrument_once_and_mark_the_other(report):
    lines = compact_paths(report)
    assert lines[0] == "Levels on NIFTY OCT FUT (MOCK) unless marked."
    body = "\n".join(lines[1:])
    assert " on NIFTY OCT FUT" not in body
    assert body.count("[NIFTY 50 (MOCK)]") == 2  # prior high / prior low are cash index
    up = next(line for line in lines if line.startswith("- UP"))
    assert up == (
        "- UP: Acceptance above VAH 25200, then above prior high 25180 "
        "[NIFTY 50 (MOCK)]. Invalid if: back below VAH 25200. Confidence: low."
    )


def test_data_gaps_are_flat_sentences(report):
    lines = gap_lines(report)
    assert (
        lines[0] == "- GIFT Nifty: unavailable. Not licensed in current feed, gate G03. "
        "Affects R02, R12."
    )
    for line in lines:
        assert not re.search(r"\([^()]*\(", line), line  # no nested brackets
