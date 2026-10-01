"""Volume profile, point of control (POC) and 70% value area (R12, AMT).

Method (fixed, versioned — S74 describes the general idea):
- Each bar's volume is split evenly across the price bins it touched (bin = `tick`).
- POC = bin with most volume. Tie -> bin nearest the profile's middle; still tied -> lower.
- Reported prices: POC = middle of its bin; VAL = lower edge of the lowest bin;
  VAH = upper edge of the highest bin (so VAL-VAH is the real price range covered).
- Value area: start at POC, repeatedly add the next single bin above or below, whichever
  has more volume (tie -> above), until >= 70% of total volume is inside.
Input must be a traded instrument with volume (e.g. index futures as a PROXY for the
cash index, which has no traded volume).
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from desk.quant.bars import Bar

PROFILE_VERSION = "VP-even-split-v1/VA70-single-row-v1/edges-v1"
VALUE_AREA_SHARE = Decimal("0.70")


@dataclass(frozen=True)
class ValueArea:
    poc: Decimal
    vah: Decimal
    val: Decimal
    va_volume: Decimal
    total_volume: Decimal
    tick: Decimal
    version: str = PROFILE_VERSION


def _bin(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def volume_profile(bars: list[Bar], tick: Decimal) -> dict[Decimal, Decimal]:
    if tick <= 0:
        raise ValueError("tick must be positive")
    profile: dict[Decimal, Decimal] = {}
    for bar in bars:
        if bar.volume is None:
            raise ValueError(f"{bar.instrument} has no volume; use a traded proxy")
        lo, hi = _bin(bar.low, tick), _bin(bar.high, tick)
        n = int((hi - lo) / tick) + 1
        share = bar.volume / n
        for i in range(n):
            price = lo + tick * i
            profile[price] = profile.get(price, Decimal(0)) + share
    return profile


def value_area(bars: list[Bar], tick: Decimal) -> ValueArea | None:
    profile = volume_profile(bars, tick)
    total = sum(profile.values(), Decimal(0))
    if not profile or total == 0:
        return None
    prices = sorted(profile)
    middle = (prices[0] + prices[-1]) / 2
    top = max(profile.values())
    poc = min((p for p in prices if profile[p] == top), key=lambda p: (abs(p - middle), p))
    i = j = prices.index(poc)
    inside = profile[poc]
    target = total * VALUE_AREA_SHARE
    while inside < target and (i > 0 or j < len(prices) - 1):
        below = profile[prices[i - 1]] if i > 0 else None
        above = profile[prices[j + 1]] if j < len(prices) - 1 else None
        if above is not None and (below is None or above >= below):
            j += 1
            inside += above
        else:
            i -= 1
            inside += below  # type: ignore[operator]
    return ValueArea(poc + tick / 2, prices[j] + tick, prices[i], inside, total, tick)
