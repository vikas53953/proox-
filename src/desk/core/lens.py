"""The fifteen lenses R01-R15 and the result each one returns (RC04)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from desk.core.facts import Fact, Gap


class LensId(StrEnum):
    R01 = "R01"
    R02 = "R02"
    R03 = "R03"
    R04 = "R04"
    R05 = "R05"
    R06 = "R06"
    R07 = "R07"
    R08 = "R08"
    R09 = "R09"
    R10 = "R10"
    R11 = "R11"
    R12 = "R12"
    R13 = "R13"
    R14 = "R14"
    R15 = "R15"


LENS_TITLES: dict[LensId, str] = {
    LensId.R01: "Overnight news",
    LensId.R02: "US / Asia / global futures",
    LensId.R03: "Oil / macro",
    LensId.R04: "Sector map",
    LensId.R05: "Large-cap focus",
    LensId.R06: "Price action",
    LensId.R07: "Liquidity / depth",
    LensId.R08: "Order flow",
    LensId.R09: "OI / F&O",
    LensId.R10: "Funding / flows",
    LensId.R11: "Sentiment",
    LensId.R12: "Auction market theory (AMT)",
    LensId.R13: "Options lens",
    LensId.R14: "Conditional outlook",
    LensId.R15: "Data / quality summary",
}


class LensStatus(StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class LensResult(BaseModel):
    """What one lens found. Status is computed from facts and gaps, never asserted."""

    model_config = ConfigDict(frozen=True)

    lens: LensId
    facts: tuple[Fact, ...] = ()
    gaps: tuple[Gap, ...] = ()
    notes: tuple[str, ...] = ()  # interpretation, clearly separate from facts
    uncertainty: str = ""

    @model_validator(mode="after")
    def _accounted_for(self) -> "LensResult":
        if not self.facts and not self.gaps:
            raise ValueError(f"{self.lens}: a lens must report facts or explain gaps")
        return self

    @property
    def status(self) -> LensStatus:
        if not self.facts:
            return LensStatus.UNAVAILABLE
        if any(g.degrades for g in self.gaps):
            return LensStatus.DEGRADED
        return LensStatus.COMPLETE

    @property
    def title(self) -> str:
        return LENS_TITLES[self.lens]
