import json
import re
from typing import Any
from urllib.parse import urlencode, urlparse

from rent_tracker.browser import CdpChrome
from rent_tracker.config import Area, Target
from rent_tracker.models import (
    AVAILABLE,
    INCOMPLETE,
    LET_AGREED,
    Coverage,
    Listing,
    coverage_status,
)
from rent_tracker.parse import postcode_district, price_text_pcm
from rent_tracker.sources import COLLECT_ERRORS, RawPages

SOURCE = "zoopla"
BASE = "https://www.zoopla.co.uk/to-rent/property/"


def split_search(search: str) -> tuple[str, str | None]:
    slug, _, radius = search.partition("@")
    return slug, radius or None


def search_url(search: str, target: Target, page: int = 1) -> str:
    slug, radius = split_search(search)
    params = {
        "beds_min": target.beds,
        "beds_max": target.beds,
        "baths_min": target.baths,
        "baths_max": target.baths,
        "price_max": target.max_pcm,
        "price_frequency": "per_month",
        "include_rented": "true",
        "results_sort": "newest_listings",
    }
    if radius:
        params["radius"] = radius
    if page > 1:
        params["pn"] = page
    return f"{BASE}{slug}/?{urlencode(params)}"


def fetch_page(browser: CdpChrome, url: str, attempts: int = 3) -> str:
    error: Exception | None = None
    for _ in range(attempts):
        result = browser.get(url)
        final = urlparse(result.final_url)
        if not final.path.startswith(urlparse(url).path):
            raise RuntimeError(
                f"location not resolved, redirected to {result.final_url}"
            )
        if "beds_min" not in final.query:
            raise RuntimeError(f"redirect dropped the filters: {result.final_url}")
        try:
            parse_page(result.raw_html)
        except ValueError as e:
            error = e
            continue
        return result.raw_html
    raise RuntimeError(f"no result data after {attempts} loads: {error}")


def _flight(html: str) -> str:
    chunks = re.findall(
        r"self\.__next_f\.push\(\[1,(\".*?\")\]\)</script>", html, re.DOTALL
    )
    return "".join(json.loads(c) for c in chunks)


def _json_after(text: str, key: str, opener: str) -> Any:
    i = text.find(f'"{key}"')
    if i < 0:
        raise ValueError(f"{key} not found")
    value, _ = json.JSONDecoder().raw_decode(text, text.find(opener, i))
    return value


def parse_page(html: str) -> tuple[int, int, list[dict]]:
    flight = _flight(html)
    pagination = _json_after(flight, "pagination", "{")
    listings = _json_after(flight, "regularListingsFormatted", "[")
    return int(pagination["totalResults"]), int(pagination["pageNumberMax"]), listings


def _feature(p: dict, icon: str) -> int | None:
    for f in p.get("features") or []:
        if f.get("iconId") == icon and isinstance(f.get("content"), int):
            return f["content"]
    return None


def _is_let_agreed(p: dict) -> bool:
    labels = [p.get("flag") or ""] + [
        t.get("content", "") if isinstance(t, dict) else str(t)
        for t in p.get("tags") or []
    ]
    return any(label.strip().lower() in {"let agreed", "let"} for label in labels)


def to_listing(p: dict, area: str) -> Listing:
    size = p.get("sizeSqft")
    if "comparables" in (p.get("sizeSource") or ""):
        size = None
    pos = p.get("pos") or {}
    return Listing(
        source=SOURCE,
        listing_id=str(p["listingId"]),
        area=area,
        address=p["address"],
        postcode_district=postcode_district(p["address"]),
        price_pcm=price_text_pcm(p.get("price") or ""),
        beds=_feature(p, "bed"),
        baths=_feature(p, "bath"),
        sq_ft=int(size) if size else None,
        status=LET_AGREED if _is_let_agreed(p) else AVAILABLE,
        url=f"https://www.zoopla.co.uk/to-rent/details/{p['listingId']}/",
        lat=pos.get("lat"),
        lng=pos.get("lng"),
    )


def collect(
    pages: RawPages, area: Area, target: Target
) -> tuple[list[Listing], list[Coverage]]:
    listings: dict[str, Listing] = {}
    coverage = []
    for search in area.zoopla:
        slug, _ = split_search(search)
        found: dict[str, Listing] = {}
        displayed = None
        note = ""
        page_no = 1
        try:
            while True:
                raw = pages.get(
                    f"{slug.replace('/', '-')}_{page_no:02d}.html",
                    search_url(search, target, page_no),
                )
                displayed, last_page, props = parse_page(raw)
                for p in props:
                    found[str(p["listingId"])] = to_listing(p, area.name)
                if page_no >= last_page or not props:
                    break
                page_no += 1
        except COLLECT_ERRORS as e:
            note = f"failed: {e}"
        status = INCOMPLETE if note else coverage_status(displayed, len(found))
        coverage.append(
            Coverage(SOURCE, area.name, search, displayed, len(found), status, note)
        )
        listings.update(found)
    return list(listings.values()), coverage
