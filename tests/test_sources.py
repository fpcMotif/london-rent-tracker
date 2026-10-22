from pathlib import Path

from rent_tracker.config import Target
from rent_tracker.models import AVAILABLE, LET_AGREED
from rent_tracker.sources import openrent, rightmove, zoopla

FIXTURES = Path(__file__).parent / "fixtures"
TARGET = Target(beds=2, baths=2, max_pcm=3000, area_radius_km=2.0)


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_rightmove_page_count_and_listing_fields() -> None:
    count, props = rightmove.parse_page(read("rightmove_stratford.html"))
    assert count == 62
    assert len(props) == 25
    by_id = {str(p["id"]): rightmove.to_listing(p, "Stratford") for p in props}
    let = by_id["94561104"]
    assert (let.price_pcm, let.status, let.postcode_district) == (
        2000,
        LET_AGREED,
        "E15",
    )
    sized = by_id["94138059"]
    assert (sized.price_pcm, sized.sq_ft, sized.status) == (2750, 1043, AVAILABLE)


def test_zoopla_page_totals() -> None:
    total, last_page, props = zoopla.parse_page(read("zoopla_wapping.html"))
    assert (total, last_page, len(props)) == (16, 1, 16)


def test_zoopla_let_and_let_agreed_flags_are_not_available() -> None:
    _, _, props = zoopla.parse_page(read("zoopla_wapping.html"))
    statuses = {p["listingId"]: zoopla.to_listing(p, "Wapping").status for p in props}
    assert statuses["71318059"] == LET_AGREED
    assert statuses["64895781"] == LET_AGREED
    assert statuses["74469743"] == AVAILABLE
    assert sum(s == LET_AGREED for s in statuses.values()) == 9


def test_zoopla_drops_size_estimated_from_comparables() -> None:
    _, _, props = zoopla.parse_page(read("zoopla_wapping.html"))
    rows = {p["listingId"]: zoopla.to_listing(p, "Wapping") for p in props}
    assert rows["74469743"].sq_ft is None
    assert rows["74135566"].sq_ft == 873


def test_openrent_filters_two_bed_two_bath_under_cap() -> None:
    rows = openrent.parse_search(read("openrent_stratford.html"))
    assert len(rows) == 200
    wanted = [r for r in rows if openrent.matches(r, TARGET)]
    assert "2968106" in {r["id"] for r in wanted}
    assert "2926275" not in {r["id"] for r in wanted}


def test_openrent_excludes_rooms_in_shared_flats() -> None:
    rows = openrent.parse_search(read("openrent_stratford.html"))
    wanted = {r["id"] for r in rows if openrent.matches(r, TARGET)}
    assert "3020952" not in wanted


def test_openrent_detail_title_and_size() -> None:
    title, size = openrent.parse_detail(read("openrent_detail_2968106.html"))
    assert title == "2 Bed Flat, Angel Lane, E15"
    assert size == 627
