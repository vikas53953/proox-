"""The delivered report record: date, cutoff, version, R01-R15, scenarios, gaps, hash."""

import hashlib
import json
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from desk.agents.reviewer import ReviewStatus
from desk.core.lens import LensResult
from desk.core.scenario import Scenario
from desk.lenses.context import ReportKind


class IncompleteReportError(ValueError):
    """A report without every lens R01-R15 accounted for can never be built (RC04)."""


class ReviewRejectedError(ValueError):
    pass


class Report(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: ReportKind
    trading_date: date
    cutoff: datetime
    version: int
    is_mock: bool
    feed: str
    model: str
    calendar_version: str
    method_versions: dict[str, str]
    lenses: tuple[LensResult, ...]
    scenarios: tuple[Scenario, ...]
    top_gaps: tuple[str, ...]
    review_status: ReviewStatus
    review_findings: tuple[str, ...]
    content_hash: str = ""

    def compute_hash(self) -> str:
        body = self.model_dump(mode="json", exclude={"content_hash"})
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
