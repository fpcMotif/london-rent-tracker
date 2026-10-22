import math

import httpx

from rent_tracker.config import Area

POSTCODES = "https://api.postcodes.io"


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


def nearest_area(
    point: tuple[float, float], areas: list[Area], radius_km: float
) -> Area | None:
    best = min(areas, key=lambda a: km_between(point, a.center))
    return best if km_between(point, best.center) <= radius_km else None


def locate_postcode(client: httpx.Client, postcode: str) -> tuple[float, float] | None:
    code = postcode.strip().upper()
    path = f"/postcodes/{code}" if " " in code or len(code) > 4 else f"/outcodes/{code}"
    resp = client.get(POSTCODES + path)
    if resp.status_code != 200:
        return None
    result = resp.json()["result"]
    if result.get("latitude") is None:
        return None
    return result["latitude"], result["longitude"]


def districts_for_points(
    client: httpx.Client, points: list[tuple[float, float]]
) -> list[str | None]:
    out: list[str | None] = []
    for i in range(0, len(points), 100):
        batch = points[i : i + 100]
        body = {
            "geolocations": [
                {"latitude": lat, "longitude": lng, "radius": 200, "limit": 1}
                for lat, lng in batch
            ]
        }
        resp = client.post(POSTCODES + "/postcodes", json=body)
        resp.raise_for_status()
        for item in resp.json()["result"]:
            hits = item.get("result") or []
            out.append(hits[0]["outcode"] if hits else None)
    return out
