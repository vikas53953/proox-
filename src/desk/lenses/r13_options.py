"""R13 Options lens: chain context (PCR, max pain, ATM IV, OI concentration).

PCR / max pain are context, not support/resistance. The aggregate chain does not reveal
dealer long/short inventory, so no gamma-exposure number is produced."""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap
from desk.quant.options import (
    OPTIONS_VERSION,
    ChainRow,
    atm_strike,
    max_pain,
    put_call_ratio_oi,
    put_call_ratio_volume,
)

GREEKS_GAP = Gap(
    topic="Greeks",
    data_class=DataClass.UNAVAILABLE,
    reason="feed provides no Greeks; pricing-model assumptions not yet chosen",
)


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
            lens=LensId.R13, gaps=(missing(DatasetKind.OPTION_CHAIN, ctx), GREEKS_GAP)
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
    atm = atm_strike(chain, dec(ds.meta["spot"]))
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
    top_ce = max(chain, key=lambda r: (r.ce_oi, -r.strike))
    top_pe = max(chain, key=lambda r: (r.pe_oi, r.strike))
    facts += [
        mk_fact(ds, "Highest call OI strike", top_ce.strike, "points", inst, cls, note=why),
        mk_fact(ds, "Highest put OI strike", top_pe.strike, "points", inst, cls, note=why),
    ]
    gaps = [GREEKS_GAP] + ([stale_gap(DatasetKind.OPTION_CHAIN, why)] if why else [])
    return LensResult(
        lens=LensId.R13,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=(
            "Aggregate chain does not disclose dealer long/short inventory; "
            "no gamma-exposure estimate is made.",
        ),
    )
