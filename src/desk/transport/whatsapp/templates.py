"""Message-template registry (S79). Outside the 24-hour window only an APPROVED template
of its actual category may be sent. While G02 is BLOCKED nothing is approved, so
proactive sends outside the window wait safely instead of being forced through.
"""

import json
from dataclasses import dataclass
from pathlib import Path

USABLE = "APPROVED"


@dataclass(frozen=True)
class Template:
    name: str
    language: str
    category: str  # as assigned by Meta — never assumed
    status: str  # DRAFT / PENDING / APPROVED / PAUSED / REJECTED / DISABLED
    purpose: str


class TemplateRegistry:
    def __init__(self, templates: list[Template]) -> None:
        self._by_purpose = {t.purpose: t for t in templates}

    @classmethod
    def load(cls, path: Path) -> "TemplateRegistry":
        raw = json.loads(path.read_text())
        return cls([Template(**t) for t in raw["templates"]])

    def usable(self, purpose: str) -> Template | None:
        t = self._by_purpose.get(purpose)
        return t if t and t.status == USABLE else None

    def why_not(self, purpose: str) -> str:
        t = self._by_purpose.get(purpose)
        if t is None:
            return f"no '{purpose}' template registered (G02)"
        return f"template '{t.name}' is {t.status}, not APPROVED (G02)"
