"""Offline check of NSE files the owner saved by hand (`python -m desk nse verify <folder>`).

Before G03 closes, the owner downloads real NSE files into a local folder and runs this.
Each file is checked against the shapes the NSE public adapter expects:

1. its name says which dataset it is (adapter file names, or NSE's own dated CSV names);
2. it decodes (UTF-8 / JSON) -> else PARSE ERROR;
3. every field / column the adapter reads is present -> else MISSING FIELD (a problem);
   fields of the hand-made shape sample that the adapter does not read are compared too,
   only as notes (EXTRA FIELD = in the file, not in the sample; MISSING FIELD ... note =
   in the sample, not in the file);
4. the adapter's own parser runs on it (types, cross-checks such as net = buy - sell,
   columns adding up to TOTAL) -> else CHECK FAILED.

This reads local files only. It does not enable the adapter (G03 stays BLOCKED), does not
touch the database and has no network code.
"""

import csv
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from desk.feeds.base import DatasetKind
from desk.feeds.nse_public import (
    BHAV_COLUMNS,
    FILES,
    OI_COLUMNS,
    PARSERS,
    NseDataError,
    _csv_rows,
    parse_cm_bhavcopy,
)

SAMPLES = Path(__file__).resolve().parents[3] / "fixtures" / "nse_public" / "2026-10-01"

# File-name patterns (case-insensitive) -> dataset. The adapter's own names come first;
# the dated CSV names are the ones NSE uses for its downloads.
NAME_PATTERNS: dict[DatasetKind, tuple[str, ...]] = {
    DatasetKind.PRE_OPEN: ("pre_open*.json",),
    DatasetKind.OPTION_CHAIN: ("option_chain*.json",),
    DatasetKind.FLOWS: ("fii_dii*.json",),
    DatasetKind.SECTORS: ("all_indices*.json",),
    DatasetKind.STOCKS: ("cm_bhavcopy*.csv", "bhavcopy_nse_cm_*.csv"),
    DatasetKind.FNO: ("fao_participant_oi*.csv",),
}

_PO = "data[].detail.preOpenMarket."
_OC = "records.data[]."
# Fields (JSON paths, lists shown as []) or columns (CSV) the adapter reads.
REQUIRED: dict[DatasetKind, tuple[str, ...]] = {
    DatasetKind.PRE_OPEN: (
        "data",
        "data[].metadata.symbol",
        _PO + "IEP",
        _PO + "totalBuyQuantity",
        _PO + "totalSellQuantity",
        _PO + "lastUpdateTime",
    ),
    DatasetKind.OPTION_CHAIN: (
        "records.timestamp",
        "records.expiryDates",
        "records.underlyingValue",
        _OC + "strikePrice",
        _OC + "expiryDate",
        *(
            f"{_OC}{side}.{f}"
            for side in ("CE", "PE")
            for f in (
                "openInterest",
                "totalTradedVolume",
                "changeinOpenInterest",
                "impliedVolatility",
            )
        ),
    ),
    DatasetKind.FLOWS: tuple(
        f"[].{f}" for f in ("category", "date", "buyValue", "sellValue", "netValue")
    ),
    DatasetKind.SECTORS: (
        "timestamp",
        *(
            f"data[].{f}"
            for f in (
                "key",
                "index",
                "last",
                "previousClose",
                "percentChange",
                "advances",
                "declines",
            )
        ),
    ),
    DatasetKind.STOCKS: BHAV_COLUMNS,
    DatasetKind.FNO: ("Client Type", *OI_COLUMNS),
}

SAMPLE_LABEL = "_comment"  # the shape samples' own label; never a field of a real file


@dataclass
class FileReport:
    name: str
    kind: DatasetKind | None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        head = f"{self.name}  [{self.kind.value if self.kind else '?'}]"
        body = [f"  {p}" for p in self.problems]
        if self.ok:
            body.insert(0, f"  OK: {self.summary}")
        return [head, *body, *(f"  {n}" for n in self.notes)]


def kind_for(name: str) -> DatasetKind | None:
    low = name.lower()
    for kind, patterns in NAME_PATTERNS.items():
        if any(fnmatch(low, p) for p in patterns):
            return kind
    return None


def json_paths(obj: Any, prefix: str = "") -> set[str]:
    """Every key path in a JSON value; list items collapse to `[]` (union over items)."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.add(p)
            out |= json_paths(v, p)
    elif isinstance(obj, list):
        for item in obj:
            out |= json_paths(item, f"{prefix}[]")
    return out


def _ancestors(path: str) -> set[str]:
    parts, out = path.split("."), set()
    for i in range(1, len(parts)):
        out.add(".".join(parts[:i]))
        out.add(".".join(parts[:i]).removesuffix("[]"))
    return out


def _parent(path: str) -> str:
    """ "data[].metadata.symbol" -> "data[].metadata"; "data[].x" -> "data"; top -> ""."""
    return (path.rsplit(".", 1)[0] if "." in path else "").removesuffix("[]")


def _csv_columns(kind: DatasetKind, text: str) -> list[str]:
    if kind is DatasetKind.FNO:
        _, _, text = text.lstrip("﻿").partition("\n")  # title line first
    header = next(csv.reader(io.StringIO(text.lstrip("﻿"))), [])
    return [c.strip() for c in header]


def _fields(kind: DatasetKind, data: bytes) -> tuple[set[str], Any]:
    """Present fields + the parsed value for the adapter's parser (raises on decode)."""
    text = data.decode("utf-8-sig")  # same as the adapter: a leading BOM is dropped
    if kind in (DatasetKind.STOCKS, DatasetKind.FNO):
        return set(_csv_columns(kind, text)), text
    raw = json.loads(text, parse_float=Decimal)
    return json_paths(raw), raw


def _sample_fields(kind: DatasetKind) -> set[str]:
    try:
        return _fields(kind, (SAMPLES / FILES[kind][0]).read_bytes())[0] - {SAMPLE_LABEL}
    except (OSError, ValueError):
        return set()  # samples not on disk: compare with the adapter's fields only


def _run_parser(kind: DatasetKind, value: Any, universe: tuple[str, ...] | None) -> str:
    if kind is DatasetKind.STOCKS:
        if universe is None:  # default: every series EQ row in the file
            universe = tuple(
                sorted({r["TckrSymb"] for r in _csv_rows(value) if r["SctySrs"] == "EQ"})
            )
        as_of, records, meta = parse_cm_bhavcopy(value, universe)
        missing = meta["symbols_missing"]
        extra = f"; universe names with no EQ row: {', '.join(missing)}" if missing else ""
        return f"{len(records)} stocks checked{extra}; as of {as_of:%Y-%m-%d %H:%M} IST"
    as_of, records, meta = PARSERS[kind](value)
    count = len(meta.get("participant_oi", ())) if kind is DatasetKind.FNO else len(records)
    return f"{count} rows checked; as of {as_of:%Y-%m-%d %H:%M} IST"


def verify_file(path: Path, universe: tuple[str, ...] | None = None) -> FileReport:
    kind = kind_for(path.name)
    rep = FileReport(path.name, kind)
    if kind is None:
        rep.problems.append("UNKNOWN FILE: the name matches no NSE dataset; not checked")
        return rep
    try:
        present, value = _fields(kind, path.read_bytes())
    except (OSError, ValueError) as exc:  # JSONDecodeError, UnicodeDecodeError
        rep.problems.append(f"PARSE ERROR: {type(exc).__name__}: {exc}"[:300])
        return rep
    required = set(REQUIRED[kind])
    sample = _sample_fields(kind)
    known = required | sample | {a for p in required | sample for a in _ancestors(p)}
    for f in REQUIRED[kind]:
        if f not in present:
            rep.problems.append(f"MISSING FIELD {f}")
    for f in sorted(sample - present - required):
        if _parent(f) in present | {""}:  # report the top absent field only
            rep.notes.append(f"MISSING FIELD {f} (note: in the shape sample only; not read)")
    for f in sorted(present - known - {SAMPLE_LABEL}):
        if _parent(f) in known | {""}:  # report the top unknown field only
            rep.notes.append(f"EXTRA FIELD {f} (note: not in the shape sample; not read)")
    if rep.problems:
        rep.problems.append("CHECK SKIPPED: adapter checks need the missing fields")
        return rep
    try:
        rep.summary = _run_parser(kind, value, universe)
    except (NseDataError, ValueError, KeyError, TypeError, AttributeError) as exc:
        rep.problems.append(f"CHECK FAILED: {type(exc).__name__}: {exc}"[:300])
    return rep


def verify_folder(
    folder: Path, universe: tuple[str, ...] | None = None
) -> tuple[list[FileReport], list[str]]:
    """Check every file directly in `folder` (hidden files and sub-folders skipped).
    Returns the per-file reports and the datasets with no file (a note, not a problem)."""
    files = sorted(p for p in Path(folder).iterdir() if p.is_file() and not p.name.startswith("."))
    reports = [verify_file(p, universe) for p in files]
    seen = {r.kind for r in reports}
    not_saved = [k.value for k in FILES if k not in seen]
    return reports, not_saved


def render(reports: list[FileReport], not_saved: list[str]) -> tuple[list[str], bool]:
    """Printable lines + overall ok (False on any problem or when no file was checked)."""
    lines = [line for r in reports for line in r.lines()]
    if not_saved:
        lines.append(f"not saved (not checked): {', '.join(not_saved)}")
    bad = sum(not r.ok for r in reports)
    ok = bool(reports) and bad == 0
    if not reports:
        lines.append("no files found")
    lines.append(
        f"RESULT: {len(reports)} files, {bad} with problems — "
        + ("OK" if ok else "NOT OK")
        + ". The adapter stays DISABLED (G03 BLOCKED)."
    )
    return lines, ok
