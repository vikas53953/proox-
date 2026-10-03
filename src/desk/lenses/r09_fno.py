"""R09 OI / F&O: contract-specific OI and change, basis, ban list, participant data.

OI rising alone is not net bullish buying or dealer inventory. Yesterday's released
report is not today's live positioning."""

from desk.core.facts import DataClass, Gap
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap
from desk.quant.options import OPTIONS_VERSION, basis, oi_change_pct

NO_BAN_LIST = Gap(
    topic="F&O ban list",
    data_class=DataClass.UNAVAILABLE,
    reason="ban list is not in the current feed's F&O data",
)
NO_CONTRACT_OI = Gap(
    topic="contract open interest",
    data_class=DataClass.UNAVAILABLE,
    reason="current feed's F&O data has no contract-level OI, settle or basis",
)


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.FNO)
    if ds is None:
        return LensResult(lens=LensId.R09, gaps=(missing(DatasetKind.FNO, ctx),))
    cls, why = session_fact_class(ctx, DatasetKind.FNO, ds)
    derived = DataClass.STALE if why else DataClass.DERIVED
    spot = ds.meta.get("spot_close")
    facts = []
    for r in ds.records:
        c = f"{r['contract']} (expiry {r['expiry']})"
        oi, prev = dec(r["oi"]), dec(r["prev_oi"])
        facts.append(mk_fact(ds, f"{c} open interest", oi, "contracts", c, cls, note=why))
        chg = oi_change_pct(oi, prev)
        if chg is not None:
            facts.append(
                mk_fact(
                    ds,
                    f"{c} OI change",
                    chg,
                    "%",
                    c,
                    derived,
                    note=why or f"method {OPTIONS_VERSION}",
                )
            )
        if r.get("underlying_is_index_spot") and spot is not None:
            pts, pct = basis(dec(r["settle"]), dec(spot))
            facts += [
                mk_fact(
                    ds,
                    f"{c} basis vs spot close",
                    pts,
                    "points",
                    c,
                    derived,
                    note=why or f"settle minus spot close; method {OPTIONS_VERSION}",
                ),
                mk_fact(
                    ds,
                    f"{c} basis vs spot close",
                    pct,
                    "% of spot",
                    c,
                    derived,
                    note=why or f"method {OPTIONS_VERSION}",
                ),
            ]
    gaps = []
    if "ban_list" in ds.meta:
        ban = ds.meta["ban_list"]
        facts.append(
            mk_fact(
                ds,
                "F&O ban list",
                ", ".join(ban) if ban else "none listed",
                "text",
                "NSE F&O",
                cls,
                note=why,
            )
        )
    else:  # not in this feed's data: never shown as "none listed"
        gaps.append(NO_BAN_LIST)
    if not ds.records:
        gaps.append(NO_CONTRACT_OI)
    for p in ds.meta.get("participant_oi", []):
        facts.append(
            mk_fact(
                ds,
                f"{p['participant']} net index futures OI",
                dec(p["net_contracts"]),
                "contracts",
                "Index futures",
                cls,
                note=why,
            )
        )
    gaps += [stale_gap(DatasetKind.FNO, why)] if why else []
    return LensResult(
        lens=LensId.R09,
        facts=tuple(facts),
        gaps=tuple(gaps),
        notes=(
            "OI rising alone is not net bullish buying or dealer inventory.",
            "Participant data is the prior session's released report, not live positioning.",
        ),
    )
