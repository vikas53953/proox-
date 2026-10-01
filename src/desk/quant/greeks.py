"""Black-Scholes-Merton Greeks for European options (NSE index options are European).

Pure Decimal maths at 50-digit precision — no floats, no external library.
Inputs are explicit and every assumption is returned as a label so the report can print
it next to the numbers. These are MODEL values, not market prices.
"""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext

GREEKS_VERSION = "bsm-european-v1"
PREC = 50
PI = Decimal("3.14159265358979323846264338327950288419716939937510")
_TINY = Decimal("1e-45")
_ONE, _TWO, _HALF = Decimal(1), Decimal(2), Decimal("0.5")
DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True)
class BSInputs:
    spot: Decimal
    strike: Decimal
    years: Decimal  # time to expiry, ACT/365
    rate: Decimal  # continuously compounded, as a fraction (0.065 = 6.5%)
    div_yield: Decimal  # continuous dividend yield, as a fraction
    vol: Decimal  # annualised volatility, as a fraction (0.12 = 12%)

    def __post_init__(self) -> None:
        for name in ("spot", "strike", "years", "vol"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class Greeks:
    price: Decimal
    delta: Decimal
    gamma: Decimal
    vega_per_vol_point: Decimal  # change in price for +1 percentage point of vol
    theta_per_day: Decimal  # change in price per calendar day
    version: str = GREEKS_VERSION


def norm_pdf(x: Decimal) -> Decimal:
    with localcontext(Context(prec=PREC)):
        return (-(x * x) / _TWO).exp() / (_TWO * PI).sqrt()


def norm_cdf(x: Decimal) -> Decimal:
    """Standard normal CDF via Marsaglia's series (all terms positive, no cancellation)."""
    with localcontext(Context(prec=PREC)):
        if x < -10:
            return Decimal(0)
        if x > 10:
            return _ONE
        term = total = x
        n = 1
        while abs(term) > _TINY:
            term = term * x * x / (2 * n + 1)
            total += term
            n += 1
        return _HALF + total * norm_pdf(x)


def black_scholes(inp: BSInputs, call: bool) -> Greeks:
    with localcontext(Context(prec=PREC)):
        s, k, t, r, q, v = inp.spot, inp.strike, inp.years, inp.rate, inp.div_yield, inp.vol
        sqrt_t = t.sqrt()
        d1 = ((s / k).ln() + (r - q + v * v / _TWO) * t) / (v * sqrt_t)
        d2 = d1 - v * sqrt_t
        disc_q, disc_r = (-q * t).exp(), (-r * t).exp()
        pdf_d1 = norm_pdf(d1)
        gamma = disc_q * pdf_d1 / (s * v * sqrt_t)
        vega = s * disc_q * pdf_d1 * sqrt_t / 100
        decay = -s * disc_q * pdf_d1 * v / (_TWO * sqrt_t)
        if call:
            price = s * disc_q * norm_cdf(d1) - k * disc_r * norm_cdf(d2)
            delta = disc_q * norm_cdf(d1)
            theta = decay - r * k * disc_r * norm_cdf(d2) + q * s * disc_q * norm_cdf(d1)
        else:
            price = k * disc_r * norm_cdf(-d2) - s * disc_q * norm_cdf(-d1)
            delta = -disc_q * norm_cdf(-d1)
            theta = decay + r * k * disc_r * norm_cdf(-d2) - q * s * disc_q * norm_cdf(-d1)
        return Greeks(price, delta, gamma, vega, theta / DAYS_PER_YEAR)
