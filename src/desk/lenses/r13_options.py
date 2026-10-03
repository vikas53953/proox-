"""R13 Options lens: chain context (PCR, max pain, ATM IV, OI concentration).

PCR / max pain are context, not support/resistance. The aggregate chain does not reveal
dealer long/short inventory, so no gamma-exposure number is produced."""

from datetime import date, datetime, time
from decimal import Decimal

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap
from desk.market_calendar import IST
from desk.quant.greeks import GREEKS_VERSION, BSInputs, black_scholes
from desk.quant.options import (
    OPTIONS_VERSION,
    ChainRow,
    atm_strike,
    max_pain,
    put_call_ratio_oi,
    put_call_ratio_volume,
)

EXPIRY_TIME_IST = time(15, 30)


def greeks_gap(reason: str) -> Gap:
    return Gap(topic="Greeks", data_class=DataClass.UNAVAILABLE, reason=reason)


def atm_greeks(ds, row, spot: Decimal) -> tuple[list, Gap | None]:
    """Black-Scholes Greeks for the ATM call and put, with every assumption labelled."""
    meta = ds.meta
    needed = ("risk_free_rate_pct", "rate_source", "dividend_yield_pct", "dividend_source")
    if not all(meta.get(key) is not None for key in needed):
        return [], greeks_gap("rate / dividend-yield inputs (with sources) not in feed")
    expiry = datetime.combine(date.fromisoformat(meta["expiry"]), EXPIRY_TIME_IST, tzinfo=IST)
    seconds = Decimal((expiry - ds.as_of).total_seconds())
    if seconds <= 0:
        return [], greeks_gap("option already expired at data time")
    years = seconds / Decimal(365 * 86400)
    rate, div = dec(meta["risk_free_rate_pct"]), dec(meta["dividend_yield_pct"])
    inst = f"{meta['underlying']} {row.strike} options, expiry {meta['expiry']}"
    facts = []
    for side, iv, is_call in (("CE", row.ce_iv, True), ("PE", row.pe_iv, False)):
        if iv is None:
            return [], greeks_gap(f"no published IV for ATM {side}")
        g = black_scholes(
            BSInputs(spot, row.strike, years, rate / 100, div / 100, iv / 100), call=is_call
        )
        note = (
            f"MODEL value ({GREEKS_VERSION}): Black-Scholes-Merton, European exercise; "
            f"spot = prior close {spot}; vol = published ATM {side} IV {iv}%; "
            f"rate {rate}% continuous ({meta['rate_source']}); dividend yield {div}% "
            f"continuous ({meta['dividend_source']}); time ACT/365 from data as-of to expiry "
            f"15:30 IST = {years.quantize(Decimal('0.0001'))} yr. Not a market price."
        )
        for label, value, unit in (
            ("delta", g.delta.quantize(Decimal("0.0001")), "ratio"),
            ("gamma", g.gamma.quantize(Decimal("0.000001")), "per point"),
            ("vega", g.vega_per_vol_point.quantize(Decimal("0.01")), "points per 1 vol pt"),
            ("theta", g.theta_per_day.quantize(Decimal("0.01")), "points per day"),
        ):
            facts.append(
                mk_fact(
                    ds,
                    f"ATM {side} {label} (model)",
                    value,
                    unit,
                    inst,
                    DataClass.DERIVED,
                    note=note,
                )
            )
    return facts, None


def to_chain(records: list[dict]) -> list[ChainRow]:
    return [
        ChainRow(
            strike=dec(r["strike"]),
            ce_oi=dec(r["ce_oi"]),
            pe_oi=dec(r["pe_oi"]),
            ce_volume=dec(r["ce_vol"]),
            pe_volume=dec(r["pe_vol"]),
            ce_oi_change=dec(r.get("ce_oi_chg", 0)),
            pe_oi_change=dec(r.get("pe_oi_chg", 0)),
            ce_iv=dec(r["ce_iv"]) if r.get("ce_iv") is not None else None,
            pe_iv=dec(r["pe_iv"]) if r.get("pe_iv") is not None else None,
        )
        for r in records
    ]


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.OPTION_CHAIN)
    if ds is None:
        return LensResult(
            lens=LensId.R13,
            gaps=(missing(DatasetKind.OPTION_CHAIN, ctx), greeks_gap("no option chain")),
        )
    cls, why = session_fact_class(ctx, DatasetKind.OPTION_CHAIN, ds)
    derived = DataClass.STALE if why else DataClass.DERIVED
    chain = to_chain(ds.records)
    inst = f"{ds.meta['underlying']} options, expiry {ds.meta['expiry']}"
    m = why or f"method {OPTIONS_VERSION}; context only, not support/resistance"
    facts = []
    for label, val, unit in (
        ("Put/call ratio (OI)", put_call_ratio_oi(chain), "ratio"),
        ("Put/call ratio (volume)", put_call_ratio_volume(chain), "ratio"),
        ("Max pain strike", max_pain(chain), "points"),
    ):
        if val is not None:
            facts.append(mk_fact(ds, label, val, unit, inst, derived, note=m))
    spot = dec(ds.meta["spot"])
    atm = atm_strike(chain, spot)
    greeks_problem = greeks_gap("no ATM strike") if atm is None else None
    if atm is not None:
        facts.append(
            mk_fact(ds, "ATM strike (vs prior spot close)", atm, "points", inst, derived, note=m)
        )
        row = next(r for r in chain if r.strike == atm)
        for side, iv in (("CE", row.ce_iv), ("PE", row.pe_iv)):
            if iv is not None:
                facts.append(
                    mk_fact(
                        ds,
                        f"ATM {side} implied volatility",
                        iv,
                        "% annualised",
                        inst,
                        cls,
                        note=why,
                    )
                )
        if why:
            greeks_problem = greeks_gap("chain is stale; Greeks not computed from stale inputs")
        else:
            greek_facts, greeks_problem = atm_greeks(ds, row, spot)
            facts += greek_facts
    top_ce = max(chain, key=lambda r: (r.ce_oi, -r.strike))
    top_pe = max(chain, key=lambda r: (r.pe_oi, r.strike))
    facts += [
        mk_fact(ds, "Highest call OI strike", top_ce.strike, "points", inst, cls, note=why),
        mk_fact(ds, "Highest put OI strike", top_pe.strike, "points", inst, cls, note=why),
    ]
    gaps = ([greeks_problem] if greeks_problem else []) + (
        [stale_gap(DatasetKind.OPTION_CHAIN, why)] if why else []
    )
    return LensResult(
        lens=LensId.R13,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=(
            "Aggregate chain does not disclose dealer long/short inventory; "
            "no gamma-exposure estimate is made.",
        ),
    )
