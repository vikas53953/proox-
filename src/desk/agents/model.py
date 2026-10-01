"""Model adapter interface (G01 BLOCKED -> only the deterministic mock exists).

Source content always reaches a model wrapped in a DataEnvelope marked untrusted:
text inside it is data to analyse, never an instruction or tool permission (RC09).
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DataEnvelope:
    """Untrusted, source-derived content. Never interpreted as instructions."""

    payload: dict[str, Any]
    trusted: bool = False


class ModelAdapter(Protocol):
    name: str
    is_mock: bool

    def complete(self, role: str, task: str, data: DataEnvelope) -> str:
        """Return the model's text answer for `task`, given only `data` as evidence."""
        ...


class MockModelAdapter:
    """Deterministic stand-in. Builds conditional paths purely from the reference levels
    in the envelope; it has no tools and cannot act on anything in the payload."""

    name = "mock-model-v2"
    is_mock = True

    def complete(self, role: str, task: str, data: DataEnvelope) -> str:
        if (role, task) != ("chief", "scenarios"):
            raise ValueError(f"mock model has no behaviour for {role}/{task}")
        p = data.payload
        lv = p.get("levels", {})
        watch = p.get("watch", [])
        events = p.get("events", [])
        contrary = p.get("contrary", [])
        if {"vah", "val", "prior_high", "prior_low"} <= lv.keys():

            def at(key: str) -> str:  # a level is never shown without its instrument
                return f"{lv[key]['value']} on {lv[key]['instrument']}"

            paths = [
                (
                    "BASE",
                    "Rotation inside prior value area",
                    f"Trade holds between VAL {at('val')} and VAH {at('vah')} after 09:15",
                    f"Sustained trade outside VAL-VAH on {lv['vah']['instrument']}",
                ),
                (
                    "UP",
                    "Upside acceptance",
                    f"Acceptance above VAH {at('vah')}, then above prior high {at('prior_high')}",
                    f"Back below VAH {at('vah')}",
                ),
                (
                    "DOWN",
                    "Downside acceptance",
                    f"Acceptance below VAL {at('val')}, then below prior low {at('prior_low')}",
                    f"Back above VAL {at('val')}",
                ),
            ]
        else:
            paths = [
                (
                    "BASE",
                    "No reference levels available",
                    "Wait for the first 30 minutes to form a range",
                    "A clear break of that range",
                ),
                (
                    "UP",
                    "Upside",
                    "Higher highs held after the first range",
                    "Price back inside the first range",
                ),
                (
                    "DOWN",
                    "Downside",
                    "Lower lows held after the first range",
                    "Price back inside the first range",
                ),
            ]
        out = [
            {
                "path": path,
                "condition": cond,
                "trigger": trig,
                "invalidation": inv,
                "confidence": "low",
                "contrary_evidence": contrary,
                "watch": watch,
                "event_windows": events,
            }
            for path, cond, trig, inv in paths
        ]
        return json.dumps(out)
