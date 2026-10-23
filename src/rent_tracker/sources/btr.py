import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from rent_tracker.browser import CdpChrome
from rent_tracker.config import Area, Operator, Target
from rent_tracker.geo import locate_postcode, nearest_area
from rent_tracker.llm import structured
from rent_tracker.models import (
    AVAILABLE,
    INCOMPLETE,
    LET_AGREED,
    Coverage,
    Listing,
    coverage_status,
)
from rent_tracker.parse import postcode_district, to_pcm
from rent_tracker.sources import COLLECT_ERRORS

SOURCE_PREFIX = "btr"
MAX_TEXT = 120_000

UNIT_SCHEMA = {
    "type": "object",
    "properties": {
        "displayed_count": {
            "type": ["integer", "null"],
            "description": "Total units or homes the page itself states, such as '30 properties'. Null if the page states none.",
        },
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit_ref": {"type": ["string", "null"]},
                    "development": {"type": ["string", "null"]},
                    "address": {"type": ["string", "null"]},
                    "postcode": {"type": ["string", "null"]},
                    "price": {"type": ["number", "null"]},
                    "price_period": {
                        "type": ["string", "null"],
                        "enum": ["pcm", "pw", None],
                    },
                    "price_is_from": {"type": "boolean"},
                    "beds": {"type": ["integer", "null"]},
                    "baths": {"type": ["integer", "null"]},
                    "sq_ft": {"type": ["integer", "null"]},
                    "status": {
                        "type": "string",
                        "enum": ["available", "let agreed", "unknown"],
                    },
                    "url": {"type": ["string", "null"]},
                },
                "required": [
                    "unit_ref",
                    "development",
                    "address",
                    "postcode",
                    "price",
                    "price_period",
                    "price_is_from",
                    "beds",
                    "baths",
                    "sq_ft",
                    "status",
                    "url",
                ],
            },
        },
    },
    "required": ["displayed_count", "units"],
}

INSTRUCTION = (
    "Stdin holds the visible text of a build-to-rent operator's listings page, followed by its link list. "
    "Return every rentable unit or home the text shows, one entry each. Copy values exactly as shown. "
    "Never estimate, infer, or average a value: use null when the text does not show it. "
    "A studio has beds 0. A price range for a whole building is not a unit, so skip it. "
    "Set price_is_from when the text says 'from'. Take url from the link list only when the link clearly belongs to that unit. "
    "Set displayed_count only from a count the page states."
)


def unit_id(operator: str, u: dict) -> str:
    if u.get("unit_ref"):
        return re.sub(
            r"\s+", "-", f"{u.get('development') or ''}-{u['unit_ref']}".strip("-")
        )
    basis = "|".join(
        str(u.get(k))
        for k in ("development", "address", "postcode", "beds", "baths", "sq_ft")
    )
    return hashlib.sha1(f"{operator}|{basis}".encode()).hexdigest()[:12]


def to_listing(
    operator: str, u: dict, page_url: str, area: str, point: tuple[float, float] | None
) -> Listing:
    address = ", ".join(
        x for x in (u.get("development"), u.get("address"), u.get("postcode")) if x
    )
    price = u.get("price")
    status = {"available": AVAILABLE, "let agreed": LET_AGREED}.get(u["status"], "")
    return Listing(
        source=f"{SOURCE_PREFIX}:{operator}",
        listing_id=unit_id(operator, u),
        area=area,
        address=address,
        postcode_district=postcode_district(u.get("postcode") or address),
        price_pcm=to_pcm(price, u.get("price_period") or "pcm") if price else None,
        beds=u.get("beds"),
        baths=u.get("baths"),
        sq_ft=u.get("sq_ft"),
        status=status,
        url=u.get("url") or page_url,
        lat=point[0] if point else None,
        lng=point[1] if point else None,
    )


Extract = Callable[[int, str], dict]
OUTSIDE = "outside"


def development_postcode(operator: Operator, u: dict) -> str | None:
    name = " ".join((u.get("development") or "").upper().split())
    known = {" ".join(k.upper().split()): v for k, v in operator.developments.items()}
    return known.get(name) or known.get("*")


def _extract(model: str, text: str, links: list[str]) -> dict:
    return structured(
        model,
        INSTRUCTION,
        f"{text[:MAX_TEXT]}\n\nLINKS:\n" + "\n".join(sorted(set(links))[:400]),
        UNIT_SCHEMA,
    )


def live_extractor(browser: CdpChrome, model: str, raw_dir: Path) -> Extract:
    def extract(i: int, url: str) -> dict:
        page = browser.get(url, expand=True)
        (raw_dir / f"page_{i}.txt").write_text(page.text)
        links = re.findall(r'href="(https?://[^"]+|/[^"]*)"', page.raw_html)
        result = {
            "final_url": page.final_url,
            "text_chars": len(page.text),
            **_extract(model, page.text, links),
        }
        (raw_dir / f"page_{i}.json").write_text(json.dumps(result, indent=1))
        return result

    return extract


def saved_extractor(model: str, raw_dir: Path) -> Extract:
    def extract(i: int, url: str) -> dict:
        saved = raw_dir / f"page_{i}.json"
        if saved.exists():
            return json.loads(saved.read_text())
        text = (raw_dir / f"page_{i}.txt").read_text()
        result = {
            "final_url": url,
            "text_chars": len(text),
            **_extract(model, text, []),
        }
        saved.write_text(json.dumps(result, indent=1))
        return result

    return extract


def collect(
    extract: Extract,
    client: httpx.Client,
    operator: Operator,
    areas: list[Area],
    target: Target,
) -> tuple[list[Listing], list[Coverage]]:
    listings = []
    coverage = []
    for i, url in enumerate(operator.urls):
        displayed = None
        units: list[dict] = []
        notes = []
        page_url = url
        try:
            extracted = extract(i, url)
            page_url = extracted["final_url"]
            if extracted["text_chars"] > MAX_TEXT:
                notes.append(f"page text cut at {MAX_TEXT} chars")
            displayed = extracted["displayed_count"]
            unique = {
                (unit_id(operator.name, u), u.get("price")): u
                for u in extracted["units"]
            }
            units = list(unique.values())
            if displayed is None:
                notes.append("page states no total, so completeness is unchecked")
        except COLLECT_ERRORS as e:
            notes.append(f"failed: {e}")
        outside = 0
        unplaced = 0
        baths_unknown = 0
        for u in units:
            if u.get("beds") == target.beds and u.get("baths") is None:
                baths_unknown += 1
            if (
                not u.get("price")
                or u.get("beds") != target.beds
                or u.get("baths") != target.baths
                or to_pcm(u["price"], u.get("price_period") or "pcm") > target.max_pcm
            ):
                continue
            postcode = u.get("postcode") or development_postcode(operator, u)
            if postcode is None:
                unplaced += 1
                continue
            point = None if postcode == OUTSIDE else locate_postcode(client, postcode)
            area = nearest_area(point, areas, target.area_radius_km) if point else None
            if area is None:
                outside += 1
                continue
            listings.append(
                to_listing(
                    operator.name,
                    {**u, "postcode": postcode},
                    page_url,
                    area.name,
                    point,
                )
            )
        if baths_unknown:
            notes.append(
                f"{baths_unknown} two-bed units dropped because the page shows no bathroom count"
            )
        if unplaced:
            notes.append(
                f"{unplaced} two-bed units dropped because neither the page nor the config gives their postcode"
            )
        if outside:
            notes.append(f"{outside} two-bed units outside the target areas")
        status = (
            INCOMPLETE
            if displayed is None
            or unplaced
            or any(n.startswith("failed") for n in notes)
            else coverage_status(displayed, len(units))
        )
        coverage.append(
            Coverage(
                f"{SOURCE_PREFIX}:{operator.name}",
                "all",
                url,
                displayed,
                len(units),
                status,
                "; ".join(notes),
            )
        )
    return listings, coverage
