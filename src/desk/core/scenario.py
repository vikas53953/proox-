"""Conditional session paths for R14 (RC06): base / up / down with observable triggers
and invalidation. No certainty language and no uncalibrated numeric probabilities."""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

BANNED_PHRASES = re.compile(
    r"\b(guarantee[sd]?|certain(ly)?|sure[- ]shot|definitely|risk[- ]free|can'?t lose|"
    r"will surely|100% (safe|sure))\b",
    re.IGNORECASE,
)
NUMERIC_PROBABILITY = re.compile(
    r"\d+(\.\d+)?\s*%\s*(chance|probab\w*|likel\w*|odds)|"
    r"(probability|odds|chance)\s+(of\s+)?\d+(\.\d+)?\s*%",
    re.IGNORECASE,
)


class ScenarioPath(StrEnum):
    BASE = "BASE"
    UP = "UP"
    DOWN = "DOWN"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def check_language(text: str) -> None:
    if BANNED_PHRASES.search(text):
        raise ValueError(f"certainty language not allowed: {text!r}")
    if NUMERIC_PROBABILITY.search(text):
        raise ValueError(f"uncalibrated numeric probability not allowed: {text!r}")


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")  # injected fields fail loudly

    path: ScenarioPath
    condition: str
    trigger: str
    invalidation: str
    confidence: Confidence
    contrary_evidence: tuple[str, ...] = ()
    watch: tuple[str, ...] = ()
    event_windows: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _language(self) -> "Scenario":
        for text in (self.condition, self.trigger, self.invalidation, *self.contrary_evidence):
            check_language(text)
        if not self.trigger.strip() or not self.invalidation.strip():
            raise ValueError("every path needs an observable trigger and an invalidation")
        return self
