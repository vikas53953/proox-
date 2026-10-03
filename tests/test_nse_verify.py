"""`python -m desk nse verify <folder>`: offline check of hand-saved NSE files against the
shapes the (disabled) NSE public adapter expects. Runs on the shape samples and on broken
copies in tmp_path; no database, no network."""

import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from desk.__main__ import main
from desk.config import GateBlockedError, load_settings
from desk.feeds.base import DatasetKind
from desk.feeds.nse_public import FILES, PARSERS, NseDataError
from desk.feeds.nse_verify import REQUIRED, kind_for, render, verify_file, verify_folder

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "nse_public" / "2026-10-01"
JSON_KINDS = [k for k in FILES if FILES[k][0].endswith(".json")]


@pytest.fixture
def folder(tmp_path):
    out = tmp_path / "nse"
    shutil.copytree(SAMPLES, out)
    return out


def run_cli(monkeypatch, capsys, *args) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["desk", "nse", "verify", *map(str, args)])
    try:
        main()
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, capsys.readouterr().out


def load(path: Path):
    return json.loads(path.read_text(), parse_float=Decimal)


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, default=str))


def drop(value, path: str):
    """Remove `path` (lists written as `[]`) from every place it occurs."""
    head, _, rest = path.partition(".")
    if head.endswith("[]") or head == "[]":
        key = head.removesuffix("[]")
        items = value[key] if key else value
        for item in items:
            drop(item, rest)
    elif rest:
        if isinstance(value.get(head), dict):
            drop(value[head], rest)
        elif isinstance(value.get(head), list):
            for item in value[head]:
                drop(item, rest)
    else:
        value.pop(head, None)


def file_of(kind: DatasetKind, folder: Path) -> Path:
    return folder / FILES[kind][0]


# ---- samples ----------------------------------------------------------------------------


def test_samples_all_ok(monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, SAMPLES)
    assert code == 0, out
    assert out.count("  OK: ") == 6
    assert "MISSING FIELD" not in out and "EXTRA FIELD" not in out
    assert "RESULT: 6 files, 0 with problems — OK" in out
    assert "DISABLED (G03 BLOCKED)" in out


def test_verify_does_not_enable_the_adapter(monkeypatch, capsys):
    # the command never reads settings; the gate still refuses nse_public at startup
    monkeypatch.setenv("DESK_FEED_ADAPTER", "nse_public")
    code, _ = run_cli(monkeypatch, capsys, SAMPLES)
    assert code == 0
    with pytest.raises(GateBlockedError, match="DISABLED"):
        load_settings({"DESK_FEED_ADAPTER": "nse_public"})


def test_no_network_imports():
    src = (Path(__file__).resolve().parents[1] / "src/desk/feeds/nse_verify.py").read_text()
    for mod in ("socket", "urllib", "http", "requests", "httpx", "ssl"):
        assert f"import {mod}" not in src and f"from {mod}" not in src


# ---- missing fields ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "path"), [(k, p) for k in JSON_KINDS for p in REQUIRED[k] if "." in p or "[]" in p]
)
def test_missing_required_json_field(folder, kind, path):
    f = file_of(kind, folder)
    value = load(f)
    drop(value, path)
    save(f, value)
    rep = verify_file(f)
    assert f"MISSING FIELD {path}" in rep.problems
    assert not rep.ok
    # drift guard: the adapter itself cannot use the file without this field
    with pytest.raises((NseDataError, KeyError, TypeError, ValueError)):
        PARSERS[kind](load(f))


@pytest.mark.parametrize(
    ("kind", "column"),
    [(DatasetKind.STOCKS, c) for c in REQUIRED[DatasetKind.STOCKS]]
    + [(DatasetKind.FNO, "Client Type"), (DatasetKind.FNO, "Total Long Contracts")],
)
def test_missing_required_csv_column(folder, kind, column):
    f = file_of(kind, folder)
    lines = f.read_text().splitlines()
    at = 1 if kind is DatasetKind.FNO else 0
    header = [c.strip() for c in lines[at].split(",")]
    i = header.index(column)
    lines[at:] = [",".join(c for j, c in enumerate(row.split(",")) if j != i) for row in lines[at:]]
    f.write_text("\n".join(lines) + "\n")
    rep = verify_file(f)
    assert f"MISSING FIELD {column}" in rep.problems
    assert any(p.startswith("CHECK SKIPPED") for p in rep.problems)


def test_missing_sample_only_field_is_a_note(folder):
    f = file_of(DatasetKind.PRE_OPEN, folder)
    value = load(f)
    drop(value, "data[].metadata.identifier")
    save(f, value)
    rep = verify_file(f)
    assert rep.ok
    assert any(n.startswith("MISSING FIELD data[].metadata.identifier (note") for n in rep.notes)


# ---- extra fields -----------------------------------------------------------------------


def test_extra_fields_are_notes_not_problems(folder, monkeypatch, capsys):
    f = file_of(DatasetKind.FLOWS, folder)
    value = load(f)
    for row in value:
        row["newColumn"] = {"deep": 1}
    save(f, value)
    rep = verify_file(f)
    assert rep.ok
    assert rep.notes == ["EXTRA FIELD [].newColumn (note: not in the shape sample; not read)"]
    bhav = file_of(DatasetKind.STOCKS, folder)
    lines = bhav.read_text().splitlines()
    bhav.write_text("\n".join([lines[0] + ",NewCol"] + [r + ",x" for r in lines[1:]]) + "\n")
    assert any("EXTRA FIELD NewCol" in n for n in verify_file(bhav).notes)
    code, out = run_cli(monkeypatch, capsys, folder)
    assert code == 0 and "EXTRA FIELD [].newColumn" in out


# ---- parse errors and failed cross-checks -----------------------------------------------


def test_broken_json_is_parse_error(folder, monkeypatch, capsys):
    f = file_of(DatasetKind.OPTION_CHAIN, folder)
    f.write_text(f.read_text()[:200])
    rep = verify_file(f)
    assert rep.problems[0].startswith("PARSE ERROR: JSONDecodeError")
    code, out = run_cli(monkeypatch, capsys, folder)
    assert code == 1 and "PARSE ERROR" in out and "NOT OK" in out


def test_non_utf8_csv_is_parse_error(folder):
    f = file_of(DatasetKind.FNO, folder)
    f.write_bytes(b"\xff\xfe" + f.read_bytes())
    assert verify_file(f).problems[0].startswith("PARSE ERROR: UnicodeDecodeError")


def test_value_that_does_not_parse(folder):
    f = file_of(DatasetKind.PRE_OPEN, folder)
    value = load(f)
    value["data"][0]["detail"]["preOpenMarket"]["IEP"] = "abc"
    save(f, value)
    (p,) = verify_file(f).problems
    assert p.startswith("CHECK FAILED") and "not a number" in p


def test_fii_net_cross_check(folder):
    f = file_of(DatasetKind.FLOWS, folder)
    value = load(f)
    value[0]["netValue"] = "1.00"
    save(f, value)
    (p,) = verify_file(f).problems
    assert p.startswith("CHECK FAILED") and "net" in p


def test_participant_total_cross_check(folder):
    f = file_of(DatasetKind.FNO, folder)
    f.write_text(f.read_text().replace("Client,210000,", "Client,210001,", 1))
    (p,) = verify_file(f).problems
    assert p.startswith("CHECK FAILED") and "TOTAL" in p


def test_sector_percent_cross_check(folder):
    f = file_of(DatasetKind.SECTORS, folder)
    value = load(f)
    value["data"][2]["percentChange"] = "9.99"
    save(f, value)
    (p,) = verify_file(f).problems
    assert p.startswith("CHECK FAILED") and "percentChange" in p


def test_bhavcopy_checks_every_eq_row_by_default(folder):
    f = file_of(DatasetKind.STOCKS, folder)
    rep = verify_file(f)
    assert rep.ok and rep.summary.startswith("3 stocks")  # BANK, OIL, SMALL (EQ only)
    f.write_text(f.read_text().replace(",210.00,214.50,208.10,", ",210.00,200.00,208.10,"))
    (p,) = verify_file(f).problems  # SAMPLE_SMALL high below its open: OHLC inconsistent
    assert "SAMPLE_SMALL" in p and "OHLC" in p
    # a narrower universe skips that row; a listed name with no row is named
    narrow = verify_file(f, ("SAMPLE_BANK", "SAMPLE_GONE"))
    assert narrow.ok and "SAMPLE_GONE" in narrow.summary


# ---- names, folders, exit codes ---------------------------------------------------------


def test_nse_dated_names_are_recognised():
    assert kind_for("BhavCopy_NSE_CM_0_0_0_20260930_F_0000.csv") is DatasetKind.STOCKS
    assert kind_for("fao_participant_oi_30092026.csv") is DatasetKind.FNO
    assert kind_for("notes.txt") is None


def test_unknown_file_is_a_problem(folder, monkeypatch, capsys):
    (folder / "random.json").write_text("{}")
    (folder / ".hidden").write_text("x")
    code, out = run_cli(monkeypatch, capsys, folder)
    assert code == 1
    assert "random.json  [?]" in out and "UNKNOWN FILE" in out and ".hidden" not in out


def test_missing_dataset_is_listed_not_failed(folder, monkeypatch, capsys):
    file_of(DatasetKind.FNO, folder).unlink()
    code, out = run_cli(monkeypatch, capsys, folder)
    assert code == 0 and "not saved (not checked): fno" in out


def test_empty_folder_and_bad_folder_fail(tmp_path, monkeypatch, capsys):
    code, out = run_cli(monkeypatch, capsys, tmp_path)
    assert code == 1 and "no files found" in out
    code, _ = run_cli(monkeypatch, capsys, tmp_path / "nope")
    assert code != 0
    assert render([], [])[1] is False


def test_folder_report_counts(folder):
    f = file_of(DatasetKind.FLOWS, folder)
    f.write_text("[]")
    reports, not_saved = verify_folder(folder)
    assert not_saved == [] and len(reports) == 6
    assert [r.name for r in reports if not r.ok] == [f.name]
