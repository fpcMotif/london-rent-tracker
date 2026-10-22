import datetime as dt
import sqlite3
from pathlib import Path

import httpx

from rent_tracker import commute, store
from rent_tracker.config import load_config
from rent_tracker.models import AVAILABLE, Listing
from rent_tracker.summary import summarise

CONFIG = load_config(Path(__file__).parents[1] / "config.toml")
DEST = {"result": {"latitude": 51.5142, "longitude": -0.0866}}


def journey(minutes: int) -> dict:
    return {
        "duration": minutes,
        "legs": [
            {
                "mode": {"name": "tube"},
                "duration": minutes,
                "routeOptions": [{"name": "Central"}],
            }
        ],
    }


def flat(lid: str, lat: float, lng: float) -> Listing:
    return Listing(
        "rightmove",
        lid,
        "Stratford/East Village",
        "x",
        "E15",
        2500,
        2,
        2,
        None,
        AVAILABLE,
        "u",
        lat,
        lng,
    )


def fake_tfl(
    times: dict[tuple[str, str], int], calls: list[str], limited_once: bool = False
) -> httpx.Client:
    state = {"limited": limited_once}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=DEST)
        if state["limited"]:
            state["limited"] = False
            return httpx.Response(429, headers={"Retry-After": "0"})
        origin = request.url.path.split("/")[3]
        mode = request.url.params["mode"]
        calls.append(f"{origin}|{mode}")
        minutes = times.get((origin, mode))
        return httpx.Response(
            200, json={"journeys": [journey(minutes)] if minutes else []}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_each_listing_gets_the_time_from_its_own_coordinates(tmp_path: Path) -> None:
    db = store.connect(tmp_path / "t.db")
    store.save_run(
        db,
        "2026-10-22",
        [flat("near", 51.5416, -0.0034), flat("far", 51.5560, 0.0050)],
        [],
    )
    calls: list[str] = []
    client = fake_tfl(
        {
            ("51.542,-0.003", commute.TRANSIT_MODES): 14,
            ("51.556,0.005", commute.TRANSIT_MODES): 41,
            ("51.542,-0.003", "cycle"): 25,
        },
        calls,
    )
    planner = commute.Planner(client, sleep=lambda s: None)
    done, failed = commute.refresh(
        db, client, CONFIG, dt.date(2026, 10, 22), print, planner
    )
    assert (done, failed) == (2, 0)
    rows = {r["listing_id"]: r for r in store.run_listings(db, "2026-10-22")}
    assert rows["near"]["transit_min"] == 14
    assert rows["near"]["cycle_min"] == 25
    assert rows["far"]["transit_min"] == 41
    assert rows["far"]["cycle_min"] is None
    summary = summarise("2026-10-22", "Stratford/East Village", list(rows.values()), 30)
    assert (summary.within_commute, summary.no_commute_time) == (1, 0)


def test_cached_points_are_not_timed_again(tmp_path: Path) -> None:
    db = store.connect(tmp_path / "t.db")
    store.save_run(db, "2026-10-22", [flat("a", 51.5416, -0.0034)], [])
    calls: list[str] = []
    client = fake_tfl({("51.542,-0.003", commute.TRANSIT_MODES): 14}, calls)
    planner = commute.Planner(client, sleep=lambda s: None)
    commute.refresh(db, client, CONFIG, dt.date(2026, 10, 22), print, planner)
    first = len(calls)
    store.save_run(db, "2026-10-29", [flat("b", 51.54162, -0.00341)], [])
    assert commute.refresh(
        db, client, CONFIG, dt.date(2026, 10, 29), print, planner
    ) == (0, 0)
    assert len(calls) == first


def test_rate_limit_response_is_retried(tmp_path: Path) -> None:
    db = store.connect(tmp_path / "t.db")
    store.save_run(db, "2026-10-22", [flat("a", 51.5416, -0.0034)], [])
    calls: list[str] = []
    client = fake_tfl(
        {("51.542,-0.003", commute.TRANSIT_MODES): 14}, calls, limited_once=True
    )
    planner = commute.Planner(client, sleep=lambda s: None)
    assert commute.refresh(
        db, client, CONFIG, dt.date(2026, 10, 22), print, planner
    ) == (1, 0)
    row = store.run_listings(db, "2026-10-22")[0]
    assert row["transit_min"] == 14


def test_old_district_table_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE commute (postcode_district TEXT PRIMARY KEY)")
    old.commit()
    old.close()
    db = store.connect(path)
    names = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "commute" not in names
    assert "commute_points" in names
