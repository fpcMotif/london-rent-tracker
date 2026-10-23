from pathlib import Path

import httpx

from rent_tracker.config import Operator, load_config
from rent_tracker.models import INCOMPLETE, OK
from rent_tracker.sources import btr

CONFIG = load_config(Path(__file__).parents[1] / "config.toml")
CANNING_TOWN = {"result": {"latitude": 51.516286, "longitude": 0.010702}}


def postcodes_io() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.replace("%20", " ").endswith("/E16 1EH"):
            return httpx.Response(200, json=CANNING_TOWN)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def unit(development: str, price: int = 2500) -> dict:
    return {
        "unit_ref": "A1",
        "development": development,
        "address": None,
        "postcode": None,
        "price": price,
        "price_period": "pcm",
        "price_is_from": False,
        "beds": 2,
        "baths": 2,
        "sq_ft": 700,
        "status": "available",
        "url": None,
    }


def run(operator: Operator, units: list[dict]):
    def extract(i: int, url: str) -> dict:
        return {
            "final_url": url,
            "text_chars": 100,
            "displayed_count": len(units),
            "units": units,
        }

    return btr.collect(extract, postcodes_io(), operator, CONFIG.areas, CONFIG.target)


def test_unit_without_any_postcode_makes_the_search_incomplete() -> None:
    listings, [cov] = run(Operator("Fizzy", ["u"]), [unit("CANNING TOWN")])
    assert listings == []
    assert cov.status == INCOMPLETE
    assert "postcode" in cov.note


def test_unit_is_placed_by_its_developments_configured_postcode() -> None:
    operator = Operator("Fizzy", ["u"], {"CANNING TOWN": "E16 1EH"})
    [listing], [cov] = run(operator, [unit("Canning  town")])
    assert listing.area == "Royal Docks/Royal Wharf"
    assert listing.postcode_district == "E16"
    assert cov.status == OK


def test_development_marked_outside_is_dropped_without_a_gap() -> None:
    operator = Operator("Tipi/Quintain", ["u"], {"*": btr.OUTSIDE})
    listings, [cov] = run(operator, [unit("LUNA")])
    assert listings == []
    assert cov.status == OK
    assert "outside" in cov.note


def test_unit_over_the_price_cap_is_not_a_gap() -> None:
    listings, [cov] = run(Operator("Fizzy", ["u"]), [unit("EAST 16", price=3400)])
    assert listings == []
    assert cov.status == OK
