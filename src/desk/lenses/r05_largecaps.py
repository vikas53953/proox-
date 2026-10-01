"""R05 Large-cap focus: scan all Nifty names for material catalysts, then a
capacity-bounded deep-focus shortlist with selection reasons. No invented opinions
for names without a catalyst."""

from datetime import datetime

from desk.core.facts import DataClass, Source
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
