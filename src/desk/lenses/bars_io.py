"""Turn bar dataset records into typed Bars."""

from datetime import datetime

from desk.feeds.base import Dataset
from desk.lenses.context import dec
from desk.quant.bars import Bar


def to_bars(ds: Dataset) -> list[Bar]:
    return [
        Bar(
            instrument=ds.meta["instrument"],
            start=datetime.fromisoformat(r["start"]),
            end=datetime.fromisoformat(r["end"]),
            open=dec(r["o"]),
            high=dec(r["h"]),
            low=dec(r["l"]),
            close=dec(r["c"]),
            volume=dec(r["v"]) if r.get("v") is not None else None,
        )
        for r in ds.records
    ]
