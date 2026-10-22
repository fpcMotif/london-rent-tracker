from dataclasses import dataclass
from statistics import median

from rent_tracker.models import LET_AGREED


@dataclass
class AreaSummary:
    run_date: str
    area: str
    count: int
    available: int
    let_agreed: int
    median_pcm: float | None
    min_pcm: int | None
    max_pcm: int | None
    median_pcm_per_sqft: float | None
    with_sqft: int
    within_commute: int
    median_pcm_within_commute: float | None
    no_commute_time: int


def summarise(
    run_date: str, area: str, rows: list, max_commute_min: int
) -> AreaSummary:
    canon = [r for r in rows if r["canonical"] and r["price_pcm"] is not None]
    prices = [r["price_pcm"] for r in canon]
    per_sqft = [r["price_pcm"] / r["sq_ft"] for r in canon if r["sq_ft"]]
    near = [
        r["price_pcm"]
        for r in canon
        if r["transit_min"] is not None and r["transit_min"] <= max_commute_min
    ]
    return AreaSummary(
        run_date=run_date,
        area=area,
        count=len(canon),
        available=sum(r["status"] != LET_AGREED for r in canon),
        let_agreed=sum(r["status"] == LET_AGREED for r in canon),
        median_pcm=median(prices) if prices else None,
        min_pcm=min(prices) if prices else None,
        max_pcm=max(prices) if prices else None,
        median_pcm_per_sqft=round(median(per_sqft), 2) if per_sqft else None,
        with_sqft=len(per_sqft),
        within_commute=len(near),
        median_pcm_within_commute=median(near) if near else None,
        no_commute_time=sum(r["transit_min"] is None for r in canon),
    )
