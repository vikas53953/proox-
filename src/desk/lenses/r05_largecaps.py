"""R05 Large-cap focus: scan all Nifty names for material catalysts, then a
capacity-bounded deep-focus shortlist with selection reasons. No invented opinions
for names without a catalyst."""

from datetime import datetime

from desk.core.facts import DataClass, Gap, Source
from desk.core.lens import LensId, LensResult
from desk.feeds.base import DatasetKind
from desk.lenses.context import LensContext, dec, missing, mk_fact, session_fact_class, stale_gap

SHORTLIST_CAPACITY = 5


def shortlist(records: list[dict]) -> list[dict]:
    with_catalyst = [r for r in records if r.get("catalysts")]
    return sorted(with_catalyst, key=lambda r: (-dec(r["weight_pct"]), r["symbol"]))[
        :SHORTLIST_CAPACITY
    ]


def build(ctx: LensContext) -> LensResult:
    ds = ctx.ds(DatasetKind.STOCKS)
    if ds is None:
        return LensResult(
            lens=LensId.R05, gaps=(missing(DatasetKind.STOCKS, ctx, "no stock focus list"),)
        )
    cls, why = session_fact_class(ctx, DatasetKind.STOCKS, ds)
    if ds.meta.get("catalysts_sourced") is False:
        return levels_only(ds, cls, why)
    # Only catalysts published by the cutoff count (no look-ahead).
    records = [
        r
        | {
            "catalysts": [
                c
                for c in r.get("catalysts") or []
                if datetime.fromisoformat(c["published_at"]) <= ctx.cutoff
            ]
        }
        for r in ds.records
    ]
    picks = shortlist(records)
    facts, notes = [], []
    for r in picks:
        sym = r["symbol"]
        facts += [
            mk_fact(ds, f"{sym} prior close", dec(r["prev_close"]), "INR", sym, cls, note=why),
            mk_fact(ds, f"{sym} prior high", dec(r["prev_high"]), "INR", sym, cls, note=why),
            mk_fact(ds, f"{sym} prior low", dec(r["prev_low"]), "INR", sym, cls, note=why),
            mk_fact(ds, f"{sym} Nifty weight", dec(r["weight_pct"]), "%", sym, cls, note=why),
        ]
        for c in r["catalysts"]:
            src = Source(id=c["id"], name=c["origin"], url=c["url"], is_mock=ds.source.is_mock)
            facts.append(
                mk_fact(
                    ds,
                    f"{sym} catalyst ({c['type']})",
                    c["headline"],
                    "text",
                    sym,
                    DataClass.OVERNIGHT,
                    source=src,
                    as_of=datetime.fromisoformat(c["published_at"]),
                )
            )
        notes.append(
            f"{sym}: shortlisted — {len(r['catalysts'])} catalyst(s), "
            f"weight {r['weight_pct']}%. Invalidation reference: prior low "
            f"{r['prev_low']} / prior high {r['prev_high']} (reference levels, not "
            f"targets)."
        )
    skipped = [r["symbol"] for r in records if r.get("catalysts") and r not in picks]
    notes.append(
        f"Scanned {len(records)} names; {len(picks)} shortlisted "
        f"(capacity {SHORTLIST_CAPACITY}); "
        f"{len(records) - len(picks) - len(skipped)} without material catalyst."
    )
    if skipped:
        notes.append(f"Catalyst but over capacity: {', '.join(skipped)}")
    gaps = [stale_gap(DatasetKind.STOCKS, why)] if why else []
    if not picks:  # a real, sourced zero — not missing data
        facts.append(
            mk_fact(
                ds,
                "Nifty names with material catalyst",
                dec(0),
                "count",
                "NIFTY 50",
                DataClass.STALE if why else DataClass.DERIVED,
                note=why,
            )
        )
    return LensResult(lens=LensId.R05, facts=tuple(facts), gaps=tuple(gaps), notes=tuple(notes))


NO_CATALYSTS = Gap(
    topic="catalysts",
    data_class=DataClass.UNAVAILABLE,
    reason="current feed has no catalyst source: no shortlist made and no catalyst count claimed",
)


def levels_only(ds, cls: DataClass, why: str) -> LensResult:
    """A feed with prior-session prices but no catalyst source (e.g. an exchange
    bhavcopy): show the scanned names' prior-session levels, never a "0 catalysts"
    count. Labels avoid "prior close" so R14 does not treat these as watch names."""
    facts = []
    for r in sorted(ds.records, key=lambda r: r["symbol"]):
        sym = r["symbol"]
        for part in ("close", "high", "low"):
            facts.append(
                mk_fact(
                    ds,
                    f"{sym} prior session {part}",
                    dec(r[f"prev_{part}"]),
                    "INR",
                    sym,
                    cls,
                    note=why,
                )
            )
    gaps = [NO_CATALYSTS] + ([stale_gap(DatasetKind.STOCKS, why)] if why else [])
    missing_syms = ds.meta.get("symbols_missing") or []
    if missing_syms:
        gaps.append(
            Gap(
                topic=DatasetKind.STOCKS.value,
                data_class=DataClass.UNAVAILABLE,
                reason=f"no prior-session row for {', '.join(missing_syms)}",
            )
        )
    notes = (
        f"Scanned {len(ds.records)} names for prior-session levels; no catalyst source, "
        "so no shortlist.",
    )
    return LensResult(lens=LensId.R05, facts=tuple(facts), gaps=tuple(gaps), notes=notes)
