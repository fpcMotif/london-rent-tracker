from rent_tracker.dedupe import dedupe
from rent_tracker.models import AVAILABLE, Listing
from rent_tracker.parse import postcode_district, price_text_pcm, sq_ft


def listing(
    source: str,
    lid: str,
    address: str,
    price: int,
    size: int | None = None,
    district: str | None = "E14",
) -> Listing:
    return Listing(
        source,
        lid,
        "Canary Wharf",
        address,
        district,
        price,
        2,
        2,
        size,
        AVAILABLE,
        "u",
    )


def test_postcode_district() -> None:
    assert postcode_district("Kendon House, Bryant Street, Stratford, E15") == "E15"
    assert postcode_district("Mawes House, London, SE17 1EQ") == "SE17"
    assert (
        postcode_district("Gerry Raffles Square | London | E15 | Elizabeth Line")
        == "E15"
    )
    assert postcode_district("12 High Street") is None


def test_price_and_size_text() -> None:
    assert price_text_pcm("£2,500 pcm") == 2500
    assert price_text_pcm("£577 pw") == 2500
    assert sq_ft("1,043 sq. ft.") == 1043
    assert sq_ft("70 sq. m.") == 753


def test_same_building_and_price_across_sources_merge() -> None:
    rows = dedupe(
        [
            listing("zoopla", "z1", "Landmark Pinnacle, Marsh Wall, E14", 2900, 800),
            listing("rightmove", "r1", "Landmark Pinnacle, London", 2900, 805),
        ]
    )
    canon = [x for x in rows if x.canonical]
    assert [x.key for x in canon] == ["rightmove:r1"]
    assert canon[0].other_source_ids == ["zoopla:z1"]


def test_no_merge_within_one_source_or_on_price_or_size_mismatch() -> None:
    rows = dedupe(
        [
            listing("rightmove", "r1", "Landmark Pinnacle", 2900, 800),
            listing("rightmove", "r2", "Landmark Pinnacle", 2900, 800),
            listing("zoopla", "z1", "Landmark Pinnacle", 2950, 800),
            listing("openrent", "o1", "Landmark Pinnacle", 2900, 1000),
        ]
    )
    assert all(x.canonical for x in rows)


def test_unclear_pairs_go_to_the_judge() -> None:
    asked = []

    def judge(pairs: list[tuple[Listing, Listing]]) -> list[bool]:
        asked.extend(pairs)
        return [True] * len(pairs)

    rows = dedupe(
        [
            listing("zoopla", "z1", "Marsh Wall", 2600),
            listing("openrent", "o1", "2 Bed Flat, Millharbour", 2600),
        ],
        judge,
    )
    assert len(asked) == 1
    assert sum(x.canonical for x in rows) == 1
