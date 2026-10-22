import datetime as dt
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from rent_tracker.config import Config
from rent_tracker.geo import locate_postcode

TFL = "https://api.tfl.gov.uk/Journey/JourneyResults/{origin}/to/{dest}"
TRANSIT_MODES = "tube,dlr,elizabeth-line,walking"
POINT_DECIMALS = 3
ANONYMOUS_INTERVAL_S = 1.4
KEYED_INTERVAL_S = 0.15
MAX_RETRIES = 3


class TflError(Exception):
    pass


@dataclass
class Journey:
    minutes: int
    route: str


def next_weekday(today: dt.date) -> dt.date:
    day = today + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def route_text(journey: dict) -> str:
    parts = []
    for leg in journey["legs"]:
        mode = leg["mode"]["name"]
        if mode == "walking":
            parts.append(f"walk {leg['duration']}m")
        else:
            names = [o["name"] for o in leg.get("routeOptions", []) if o.get("name")]
            parts.append(f"{', '.join(names) or mode} {leg['duration']}m")
    return " > ".join(parts)


class Planner:
    def __init__(
        self, client: httpx.Client, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.app_key = os.environ.get("TFL_APP_KEY")
        self.interval = KEYED_INTERVAL_S if self.app_key else ANONYMOUS_INTERVAL_S
        self._last = 0.0

    def fastest(
        self, origin: tuple[float, float], dest: tuple[float, float], params: dict
    ) -> Journey | None:
        url = TFL.format(origin=f"{origin[0]},{origin[1]}", dest=f"{dest[0]},{dest[1]}")
        if self.app_key:
            params = {**params, "app_key": self.app_key}
        for _ in range(MAX_RETRIES):
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            resp = self.client.get(url, params=params)
            if resp.status_code == 429:
                self.sleep(float(resp.headers.get("Retry-After", 60)))
                continue
            if resp.status_code in (300, 404):
                return None
            if resp.status_code != 200:
                raise TflError(f"TfL HTTP {resp.status_code}")
            journeys = resp.json().get("journeys") or []
            if not journeys:
                return None
            best = min(journeys, key=lambda j: j["duration"])
            return Journey(best["duration"], route_text(best))
        raise TflError("TfL rate limit did not clear")


def point_key(lat: float, lng: float) -> tuple[float, float]:
    return round(lat, POINT_DECIMALS), round(lng, POINT_DECIMALS)


def refresh(
    db: sqlite3.Connection,
    client: httpx.Client,
    cfg: Config,
    today: dt.date,
    log: Callable[[str], None],
    planner: Planner | None = None,
) -> tuple[int, int]:
    points = [
        (r[0], r[1])
        for r in db.execute(
            f"""
            SELECT DISTINCT ROUND(lat, {POINT_DECIMALS}), ROUND(lng, {POINT_DECIMALS})
            FROM sightings WHERE lat IS NOT NULL AND lng IS NOT NULL
            EXCEPT SELECT lat, lng FROM commute_points
            """
        )
    ]
    if not points:
        return 0, 0
    dest = locate_postcode(client, cfg.destination.postcode)
    if dest is None:
        raise TflError(f"cannot locate {cfg.destination.postcode}")
    planner = planner or Planner(client)
    base = {
        "date": next_weekday(today).strftime("%Y%m%d"),
        "time": cfg.destination.arrive_time,
        "timeIs": "Arriving",
    }
    log(f"commute: {len(points)} new points to time")
    done = failed = 0
    for n, point in enumerate(points, 1):
        try:
            transit = planner.fastest(point, dest, {**base, "mode": TRANSIT_MODES})
            cycle = planner.fastest(point, dest, {**base, "mode": "cycle"})
        except (TflError, httpx.HTTPError) as e:
            failed += 1
            log(f"commute: point {point} failed, will retry next run: {e}")
            continue
        with db:
            db.execute(
                "INSERT OR REPLACE INTO commute_points VALUES (?,?,?,?,?,?)",
                (
                    point[0],
                    point[1],
                    transit.minutes if transit else None,
                    cycle.minutes if cycle else None,
                    transit.route if transit else "no Tube/DLR/Elizabeth line journey",
                    today.isoformat(),
                ),
            )
        done += 1
        if n % 50 == 0:
            log(f"commute: {n}/{len(points)} points timed")
    return done, failed
