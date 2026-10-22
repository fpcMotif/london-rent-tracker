import dataclasses
import shutil
from pathlib import Path

import pytest

from rent_tracker import load_checkpoint, save_checkpoint
from rent_tracker.config import load_config
from rent_tracker.models import INCOMPLETE, LET_AGREED, Coverage, Listing
from rent_tracker.sources import RawPages, openrent

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_config(Path(__file__).parents[1] / "config.toml")
STRATFORD = dataclasses.replace(
    next(a for a in CONFIG.areas if a.name.startswith("Stratford")),
    openrent=["Stratford, London"],
)


def test_live_pages_are_saved_and_replayed_without_fetching(tmp_path: Path) -> None:
    live = RawPages(tmp_path, lambda url: f"page for {url}")
    assert live.get("a.html", "https://x/a") == "page for https://x/a"
    replay = RawPages(tmp_path, None)
    assert replay.get("a.html", "https://x/a") == "page for https://x/a"


def test_replay_of_a_missing_page_fails(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        RawPages(tmp_path, None).get("missing.html", "https://x/missing")


def test_openrent_rebuilds_rows_from_saved_pages(tmp_path: Path) -> None:
    shutil.copy(
        FIXTURES / "openrent_stratford.html", tmp_path / "stratford-london.html"
    )
    shutil.copy(
        FIXTURES / "openrent_detail_2968106.html", tmp_path / "detail_2968106.html"
    )
    listings, coverage = openrent.collect(
        RawPages(tmp_path, None), STRATFORD, CONFIG.target
    )
    by_id = {x.listing_id: x for x in listings}
    assert by_id["2968106"].address == "2 Bed Flat, Angel Lane, E15"
    assert by_id["2968106"].sq_ft == 627
    [cov] = coverage
    assert cov.collected == 1
    assert cov.status == INCOMPLETE
    assert "detail" in cov.note


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    listing = Listing(
        "zoopla",
        "1",
        "Stratford/East Village",
        "Angel Lane",
        "E15",
        2500,
        2,
        2,
        627,
        LET_AGREED,
        "u",
        51.54,
        -0.003,
        False,
        ["rightmove:9"],
    )
    cov = Coverage("zoopla", "Stratford/East Village", "london/stratford", 3, 3, "OK")
    path = tmp_path / "run" / "collected.json"
    save_checkpoint(path, [listing], [cov])
    assert load_checkpoint(path) == ([listing], [cov])
