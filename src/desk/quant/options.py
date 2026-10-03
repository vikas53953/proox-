"""Option-chain context numbers (R13). Context only — not support/resistance (S73).

Nothing here estimates dealer inventory or gamma exposure: an aggregate chain does not
disclose who is long or short, so those numbers are never produced.
"""

from dataclasses import dataclass
from decimal import Decimal

OPTIONS_VERSION = "options-v1"
TWO_DP = Decimal("0.01")


@dataclass(frozen=True)
class ChainRow:
    strike: Decimal
    ce_oi: Decimal
    pe_oi: Decimal
    ce_volume: Decimal
    pe_volume: Decimal
    ce_oi_change: Decimal = Decimal(0)
    pe_oi_change: Decimal = Decimal(0)
    ce_iv: Decimal | None = None  # % as published; None if not provided
    pe_iv: Decimal | None = None


def put_call_ratio_oi(chain: list[ChainRow]) -> Decimal | None:
    ce = sum((r.ce_oi for r in chain), Decimal(0))
    return None if ce == 0 else (sum((r.pe_oi for r in chain), Decimal(0)) / ce).quantize(TWO_DP)


def put_call_ratio_volume(chain: list[ChainRow]) -> Decimal | None:
    ce = sum((r.ce_volume for r in chain), Decimal(0))
    pe = sum((r.pe_volume for r in chain), Decimal(0))
    return None if ce == 0 else (pe / ce).quantize(TWO_DP)


def max_pain(chain: list[ChainRow]) -> Decimal | None:
    """Expiry price (among listed strikes) with the least total option-holder payout."""
    if not chain:
        return None

    def payout(settle: Decimal) -> Decimal:
        return sum(
            (
                r.ce_oi * max(Decimal(0), settle - r.strike)
                + r.pe_oi * max(Decimal(0), r.strike - settle)
                for r in chain
            ),
            Decimal(0),
        )

    return min((r.strike for r in chain), key=lambda s: (payout(s), s))


def atm_strike(chain: list[ChainRow], spot: Decimal) -> Decimal | None:
    if not chain:
        return None
    return min((r.strike for r in chain), key=lambda s: (abs(s - spot), s))


def basis(futures: Decimal, spot: Decimal) -> tuple[Decimal, Decimal]:
    """Futures minus spot, in points and in % of spot."""
    pts = futures - spot
    return pts, (pts / spot * 100).quantize(TWO_DP)


def oi_change_pct(today: Decimal, previous: Decimal) -> Decimal | None:
    return None if previous == 0 else ((today - previous) / previous * 100).quantize(TWO_DP)
