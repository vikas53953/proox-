"""Fixture feed: reads dated MOCK scenario files from fixtures/market/<date>/<scenario>/.

A scenario may name a `base` scenario and then replace files, `drop` datasets, or
override capabilities — so each failure case is a small, readable diff on the full mock.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from desk.core.facts import Source
from desk.feeds.base import Capabilities, Capability, Dataset, DatasetKind


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(), parse_float=Decimal)


class FixtureFeed:
    is_mock = True

    def __init__(self, root: Path, trading_date: date, scenario: str) -> None:
        self.name = f"fixture:{trading_date.isoformat()}/{scenario}"
        self._day_dir = root / trading_date.isoformat()
        self._trading_date = trading_date
        self._chain = self._resolve_chain(scenario)
        manifest = self._merged_manifest()
        if not manifest.get("is_mock", False):
            raise ValueError(f"fixture {scenario} must be marked is_mock")
        self._dropped = {DatasetKind(k) for k in manifest.get("drop", [])}
        self._caps = Capabilities(
            {
                DatasetKind(k): Capability(v["granted"], v["rights_note"], v.get("tier", ""))
                for k, v in manifest["capabilities"].items()
            }
        )

    def _resolve_chain(self, scenario: str) -> list[Path]:
        chain, seen = [], set()
        while scenario:
            if scenario in seen:
                raise ValueError(f"fixture base loop at {scenario}")
            seen.add(scenario)
            d = self._day_dir / scenario
            if not (d / "manifest.json").exists():
                raise FileNotFoundError(d / "manifest.json")
            chain.append(d)
            scenario = _load_json(d / "manifest.json").get("base", "")
        return chain  # most specific first

    def _merged_manifest(self) -> dict[str, Any]:
        merged: dict[str, Any] = {"capabilities": {}, "drop": []}
        for d in reversed(self._chain):
            m = _load_json(d / "manifest.json")
            merged["capabilities"].update(m.get("capabilities", {}))
            merged["drop"] = sorted(set(merged["drop"]) | set(m.get("drop", [])))
            merged["is_mock"] = m.get("is_mock", merged.get("is_mock", False))
        return merged

    def capabilities(self) -> Capabilities:
        return self._caps

    def fetch(self, kind: DatasetKind, trading_date: date) -> Dataset | None:
        if trading_date != self._trading_date:
            return None  # never serve another day's file as today's
        if kind in self._dropped:
            return None
        for d in self._chain:
            path = d / f"{kind.value}.json"
            if path.exists():
                raw = _load_json(path)
                return Dataset(
                    kind=kind,
                    source=Source(**raw["source"]),
                    as_of=datetime.fromisoformat(raw["as_of"]),
                    records=raw["records"],
                    meta=raw.get("meta", {}),
                )
        return None
