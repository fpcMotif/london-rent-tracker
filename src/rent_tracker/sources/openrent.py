import re
from html import unescape
from urllib.parse import urlencode

from rent_tracker.config import Area, Target
from rent_tracker.geo import km_between
from rent_tracker.models import (
    AVAILABLE,
    INCOMPLETE,
    LET_AGREED,
    Coverage,
    Listing,
    coverage_status,
)
from rent_tracker.parse import postcode_district, sq_ft
from rent_tracker.sources import COLLECT_ERRORS, RawPages

SOURCE = "openrent"
BASE = "https://www.openrent.co.uk/properties-to-rent/"
RADIUS_KM = 1
ID_CAP = 200
ARRAYS = {
    "id": "PROPERTYIDS",
    "price": "prices",
    "beds": "bedrooms",
    "baths": "bathrooms",
    "live": "islivelistBool",
    "shared": "isshared",
    "lat": "PROPERTYLISTLATITUDES",
    "lng": "PROPERTYLISTLONGITUDES",
}


def search_url(term: str, target: Target) -> str:
    params = {
        "term": term,
        "area": RADIUS_KM,
        "prices_max": target.max_pcm,
        "bedrooms_min": target.beds,
        "bedrooms_max": target.beds,
    }
    return f"{BASE}?{urlencode(params)}"


def parse_search(html: str) -> list[dict[str, str]]:
    columns = {}
    for field, var in ARRAYS.items():
        m = re.search(rf"var {var} = \[(.*?)\];", html, re.DOTALL)
        if not m:
            raise ValueError(f"{var} missing from page")
        columns[field] = [
            x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()
        ]
    lengths = {len(v) for v in columns.values()}
    if len(lengths) != 1:
        raise ValueError(f"OpenRent arrays differ in length: {lengths}")
    return [dict(zip(columns, row)) for row in zip(*columns.values())]


def matches(row: dict[str, str], target: Target) -> bool:
    return (
        row["beds"] == str(target.beds)
        and row["baths"] == str(target.baths)
        and row["shared"] == "0"
        and float(row["price"]) <= target.max_pcm
    )


def near_area(row: dict[str, str], area: Area, target: Target) -> bool:
    point = (float(row["lat"]), float(row["lng"]))
    return km_between(point, area.center) <= target.area_radius_km


def parse_detail(html: str) -> tuple[str, int | None]:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    title = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
    return title, sq_ft(html)


def collect(
    pages: RawPages, area: Area, target: Target
) -> tuple[list[Listing], list[Coverage]]:
    listings: dict[str, Listing] = {}
    coverage = []
    for term in area.openrent:
        found: dict[str, Listing] = {}
        displayed = None
        notes = []
        try:
            slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
            rows = parse_search(pages.get(f"{slug}.html", search_url(term, target)))
            if len(rows) >= ID_CAP:
                notes.append(f"search returned {len(rows)} ids, the list may be capped")
            wanted = [
                r for r in rows if matches(r, target) and near_area(r, area, target)
            ]
            displayed = len(wanted)
            for r in wanted:
                try:
                    detail = pages.get(
                        f"detail_{r['id']}.html",
                        f"https://www.openrent.co.uk/{r['id']}",
                    )
                except COLLECT_ERRORS as e:
                    notes.append(f"detail {r['id']} failed: {e}")
                    continue
                title, size = parse_detail(detail)
                found[r["id"]] = Listing(
                    source=SOURCE,
                    listing_id=r["id"],
                    area=area.name,
                    address=title,
                    postcode_district=postcode_district(title),
                    price_pcm=round(float(r["price"])),
                    beds=int(r["beds"]),
                    baths=int(r["baths"]),
                    sq_ft=size,
                    status=AVAILABLE if r["live"] == "1" else LET_AGREED,
                    url=f"https://www.openrent.co.uk/{r['id']}",
                    lat=float(r["lat"]),
                    lng=float(r["lng"]),
                )
        except COLLECT_ERRORS as e:
            notes.append(f"failed: {e}")
        status = coverage_status(displayed, len(found))
        if notes:
            status = INCOMPLETE
        coverage.append(
            Coverage(
                SOURCE, area.name, term, displayed, len(found), status, "; ".join(notes)
            )
        )
        listings.update(found)
    return list(listings.values()), coverage
