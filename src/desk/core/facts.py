"""Facts and gaps — the smallest units of a report.

RC03: a Fact cannot exist without source, as-of time, instrument and unit.
"N/A is not zero": missing data is a Gap with a reason; a Gap has no value field at all.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class DataClass(StrEnum):
    """What kind of data a value is. Shown next to every value in the report."""

    PRIOR_SESSION = "PRIOR SESSION"
    OVERNIGHT = "OVERNIGHT"
    LIVE_GLOBAL = "LIVE GLOBAL"
    INDICATIVE_AUCTION = "INDICATIVE AUCTION"
    DERIVED = "DERIVED"
    PROXY = "PROXY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT APPLICABLE"


VALUE_CLASSES = frozenset(DataClass) - {DataClass.UNAVAILABLE, DataClass.NOT_APPLICABLE}
GAP_CLASSES = frozenset({DataClass.UNAVAILABLE, DataClass.STALE, DataClass.NOT_APPLICABLE})


class Source(BaseModel):
    """Where a value came from. `url` is the original source, not a re-post."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    url: str
    is_mock: bool = False

    @field_validator("url")
    @classmethod
    def _url_is_http(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("source url must be http(s)")
        return v


def _require_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return v.astimezone(UTC)


class Fact(BaseModel):
    """One sourced value. Text values (e.g. a headline) use unit='text'."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: Decimal | str
    unit: str
    instrument: str
    source: Source
    as_of: datetime
    data_class: DataClass
    note: str = ""  # required for PROXY (method/limits) and STALE (age)

    _utc = field_validator("as_of")(_require_utc)

    @field_validator("value", mode="before")
    @classmethod
    def _no_float(cls, v: object) -> object:
        # Checked before pydantic would silently convert 1.5 -> Decimal("1.5").
        if isinstance(v, float):
            raise ValueError("floats not allowed; use Decimal")
        return v

    @model_validator(mode="after")
    def _rules(self) -> "Fact":
        if self.data_class not in VALUE_CLASSES:
            raise ValueError(f"{self.data_class} cannot carry a value; use a Gap")
        for name in ("label", "unit", "instrument"):
            if not getattr(self, name).strip():
                raise ValueError(f"fact {name} is required")
        if self.data_class in (DataClass.PROXY, DataClass.STALE) and not self.note.strip():
            raise ValueError(f"{self.data_class} fact needs a note (method/limits or age)")
        return self


class Gap(BaseModel):
    """Something the report could not show, and why. Deliberately has no value field."""

    model_config = ConfigDict(frozen=True)

    topic: str
    data_class: DataClass
    reason: str
    effect: str = ""

    @model_validator(mode="after")
    def _rules(self) -> "Gap":
        if self.data_class not in GAP_CLASSES:
            raise ValueError(f"gap class must be one of {sorted(GAP_CLASSES)}")
        if not self.reason.strip():
            raise ValueError("gap needs a reason")
        return self

    @property
    def degrades(self) -> bool:
        """NOT APPLICABLE is honest context, not a failure; the others degrade the lens."""
        return self.data_class is not DataClass.NOT_APPLICABLE
