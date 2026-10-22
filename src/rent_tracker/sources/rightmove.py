import json
import re
from urllib.parse import urlencode

from rent_tracker.config import Area, Target
from rent_tracker.models import (
    AVAILABLE,
    INCOMPLETE,
    LET_AGREED,
    Coverage,
    Listing,
    coverage_status,
)
from rent_tracker.parse import postcode_district, sq_ft, to_pcm
from rent_tracker.sources import COLLECT_ERRORS, RawPages

SOURCE = "rightmove"
BASE = "https://www.rightmove.co.uk/property-to-rent/find.html"
PAGE_SIZE = 24


def search_url(location: str, target: Target, index: int = 0) -> str:
    params = {
        "locationIdentifier": location,
        "minBedrooms": target.beds,
        "maxBedrooms": target.beds,
        "minBathrooms": target.baths,
        "maxBathrooms": target.baths,
        "maxPrice": target.max_pcm,
        "includeLetAgreed": "true",
        "sortType": 6,
        "radius": "0.5" if location.startswith("STATION") else "0.0",
    }
    if index:
        params["index"] = index
    return f"{BASE}?{urlencode(params)}"


def parse_page(html: str) -> tuple[int, list[dict]]:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise ValueError("no __NEXT_DATA__ on page")
    results = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]
    return int(results["resultCount"]), results["properties"]


def to_listing(p: dict, area: str) -> Listing:
    price = p["price"]
    address = p["displayAddress"]
    loc = p.get("location") or {}
    return Listing(
        source=SOURCE,
        listing_id=str(p["id"]),
        area=area,
        address=address,
        postcode_district=postcode_district(address),
        price_pcm=to_pcm(
            price["amount"], "pw" if price.get("frequency") == "weekly" else "pcm"
        ),
        beds=p.get("bedrooms"),
        baths=p.get("bathrooms"),
        sq_ft=sq_ft(p.get("displaySize") or ""),
        status=LET_AGREED
        if (p.get("displayStatus") or "").lower() == "let agreed"
        else AVAILABLE,
        url="https://www.rightmove.co.uk/properties/" + str(p["id"]),
        lat=loc.get("latitude"),
        lng=loc.get("longitude"),
    )


def collect(
    pages: RawPages, area: Area, target: Target
) -> tuple[list[Listing], list[Coverage]]:
    listings: dict[str, Listing] = {}
    coverage = []
    for location in area.rightmove:
        found: dict[str, Listing] = {}
        displayed = None
        note = ""
        index = 0
        try:
            while True:
                html = pages.get(
                    f"{location.replace('^', '-')}_{index:04d}.html",
                    search_url(location, target, index),
                )
                displayed, props = parse_page(html)
                for p in props:
                    if not p.get("featuredProperty"):
                        found[str(p["id"])] = to_listing(p, area.name)
                index += PAGE_SIZE
                if index >= displayed or not props:
                    break
        except COLLECT_ERRORS as e:
            note = f"failed: {e}"
        status = INCOMPLETE if note else coverage_status(displayed, len(found))
        coverage.append(
            Coverage(SOURCE, area.name, location, displayed, len(found), status, note)
        )
        listings.update(found)
    return list(listings.values()), coverage
