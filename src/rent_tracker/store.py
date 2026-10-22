import json
import sqlite3
from pathlib import Path

from rent_tracker.models import Coverage, Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    run_date TEXT NOT NULL,
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    area TEXT NOT NULL,
    address TEXT,
    postcode_district TEXT,
    price_pcm INTEGER,
    beds INTEGER,
    baths INTEGER,
    sq_ft INTEGER,
    status TEXT,
    url TEXT,
    lat REAL,
    lng REAL,
    canonical INTEGER NOT NULL,
    other_source_ids TEXT NOT NULL,
    PRIMARY KEY (run_date, source, listing_id)
);
CREATE TABLE IF NOT EXISTS coverage (
    run_date TEXT NOT NULL,
    source TEXT NOT NULL,
    area TEXT NOT NULL,
    search TEXT NOT NULL,
    displayed INTEGER,
    collected INTEGER NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (run_date, source, area, search)
);
DROP TABLE IF EXISTS commute;
CREATE TABLE IF NOT EXISTS commute_points (
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    transit_min INTEGER,
    cycle_min INTEGER,
    route TEXT,
    checked_on TEXT NOT NULL,
    PRIMARY KEY (lat, lng)
);
CREATE TABLE IF NOT EXISTS ons (
    month TEXT NOT NULL,
    borough TEXT NOT NULL,
    two_bed_pcm INTEGER,
    two_bed_annual_change REAL,
    all_pcm INTEGER,
    PRIMARY KEY (month, borough)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def save_run(
    db: sqlite3.Connection,
    run_date: str,
    listings: list[Listing],
    coverage: list[Coverage],
) -> None:
    with db:
        db.execute("DELETE FROM sightings WHERE run_date = ?", (run_date,))
        db.execute("DELETE FROM coverage WHERE run_date = ?", (run_date,))
        db.executemany(
            "INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_date,
                    x.source,
                    x.listing_id,
                    x.area,
                    x.address,
                    x.postcode_district,
                    x.price_pcm,
                    x.beds,
                    x.baths,
                    x.sq_ft,
                    x.status,
                    x.url,
                    x.lat,
                    x.lng,
                    int(x.canonical),
                    json.dumps(x.other_source_ids),
                )
                for x in listings
            ],
        )
        db.executemany(
            "INSERT INTO coverage VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    run_date,
                    c.source,
                    c.area,
                    c.search,
                    c.displayed,
                    c.collected,
                    c.status,
                    c.note,
                )
                for c in coverage
            ],
        )


def load_run(
    db: sqlite3.Connection, run_date: str
) -> tuple[list[Listing], list[Coverage]]:
    listings = [
        Listing(
            source=r["source"],
            listing_id=r["listing_id"],
            area=r["area"],
            address=r["address"],
            postcode_district=r["postcode_district"],
            price_pcm=r["price_pcm"],
            beds=r["beds"],
            baths=r["baths"],
            sq_ft=r["sq_ft"],
            status=r["status"],
            url=r["url"],
            lat=r["lat"],
            lng=r["lng"],
        )
        for r in run_listings(db, run_date)
    ]
    coverage = [
        Coverage(
            r["source"],
            r["area"],
            r["search"],
            r["displayed"],
            r["collected"],
            r["status"],
            r["note"] or "",
        )
        for r in coverage_rows(db, run_date)
    ]
    return listings, coverage


def run_dates(db: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in db.execute("SELECT DISTINCT run_date FROM sightings ORDER BY run_date")
    ]


COMMUTE_JOIN = """
    LEFT JOIN commute_points c
    ON c.lat = ROUND(s.lat, 3) AND c.lng = ROUND(s.lng, 3)
"""


def listing_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        f"""
        SELECT s.*, f.first_seen, f.last_seen, c.transit_min, c.cycle_min, c.route
        FROM sightings s
        JOIN (
            SELECT source, listing_id, MIN(run_date) AS first_seen, MAX(run_date) AS last_seen
            FROM sightings GROUP BY source, listing_id
        ) f USING (source, listing_id)
        {COMMUTE_JOIN}
        ORDER BY s.run_date, s.area, s.price_pcm
        """
    ).fetchall()


def run_listings(db: sqlite3.Connection, run_date: str) -> list[sqlite3.Row]:
    return db.execute(
        f"""
        SELECT s.*, c.transit_min, c.cycle_min
        FROM sightings s {COMMUTE_JOIN}
        WHERE s.run_date = ?
        """,
        (run_date,),
    ).fetchall()


def coverage_rows(
    db: sqlite3.Connection, run_date: str | None = None
) -> list[sqlite3.Row]:
    if run_date:
        return db.execute(
            "SELECT * FROM coverage WHERE run_date = ? ORDER BY source, area",
            (run_date,),
        ).fetchall()
    return db.execute(
        "SELECT * FROM coverage ORDER BY run_date, source, area"
    ).fetchall()
