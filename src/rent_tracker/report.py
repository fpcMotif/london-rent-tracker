import sqlite3

from rent_tracker.config import Config
from rent_tracker.llm import LlmError, write_with_search
from rent_tracker.models import INCOMPLETE, LET_AGREED
from rent_tracker.store import coverage_rows, run_dates, run_listings
from rent_tracker.summary import summarise

SHIFT_THRESHOLD = 0.03

RISK_INSTRUCTION = (
    "You write the risks section of a weekly London rental report. The renter wants a 2-bed 2-bath flat at or under "
    "£3,000 pcm within 30 minutes of Bank (EC2N 2AX), moving in late 2027. Stdin holds this week's facts. "
    "Search the web for current news on: London rent trend, the Renters' Rights Act and its commencement dates, "
    "new build-to-rent openings in the listed areas, and transport works affecting the Central, Northern, DLR, "
    "Jubilee and Elizabeth lines or Bank station. Write at most 250 words as Markdown bullets under the heading "
    "'## Risks'. Each bullet states one fact, its date, and its source link. Mark anything you could not confirm."
)


def _fmt(n: float | None) -> str:
    return "" if n is None else f"£{n:,.0f}"


def _row_key(r: sqlite3.Row) -> tuple[str, str]:
    return r["source"], r["listing_id"]


def facts(db: sqlite3.Connection, cfg: Config, run_date: str) -> str:
    dates = run_dates(db)
    prev = max((d for d in dates if d < run_date), default=None)
    now = run_listings(db, run_date)
    before = {_row_key(r): r for r in run_listings(db, prev)} if prev else {}
    seen_before = {
        (r[0], r[1])
        for r in db.execute(
            "SELECT source, listing_id FROM sightings WHERE run_date < ?", (run_date,)
        )
    }
    out = [f"# London 2-bed rent tracker: run {run_date}", ""]
    out.append(f"Previous run: {prev or 'none, so this run is the baseline'}.")
    out.append("")

    limit = cfg.destination.max_commute_min
    out += [
        "## Area summary",
        "",
        (
            "Commute is the fastest Tube/DLR/Elizabeth line journey from each flat, "
            f"arriving {cfg.destination.arrive_time} on a weekday."
        ),
        "",
        f"| Area | Count | Available | Let agreed | Median | Min | Max | £/sq ft | ≤{limit} min | Median ≤{limit} min | Median shift |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    prev_rows = run_listings(db, prev) if prev else []
    shifts = []
    no_time = 0
    for area in cfg.areas:
        s = summarise(
            run_date, area.name, [r for r in now if r["area"] == area.name], limit
        )
        no_time += s.no_commute_time
        p = (
            summarise(
                prev, area.name, [r for r in prev_rows if r["area"] == area.name], limit
            )
            if prev
            else None
        )
        shift = ""
        if p and p.median_pcm and s.median_pcm:
            change = (s.median_pcm - p.median_pcm) / p.median_pcm
            shift = f"{change:+.1%}"
            if abs(change) > SHIFT_THRESHOLD:
                shifts.append(
                    f"- {area.name}: median {_fmt(p.median_pcm)} to {_fmt(s.median_pcm)} ({change:+.1%})"
                )
        out.append(
            f"| {area.name} | {s.count} | {s.available} | {s.let_agreed} | {_fmt(s.median_pcm)} | {_fmt(s.min_pcm)} | "
            f"{_fmt(s.max_pcm)} | {s.median_pcm_per_sqft or ''} | {s.within_commute} | "
            f"{_fmt(s.median_pcm_within_commute)} | {shift} |"
        )
    out += ["", f"Listings without a commute time: {no_time}.", ""]

    canon_now = [r for r in now if r["canonical"]]
    new = [r for r in canon_now if _row_key(r) not in seen_before]
    drops = []
    status_changes = []
    for r in now:
        old = before.get(_row_key(r))
        if not old:
            continue
        if old["price_pcm"] and r["price_pcm"] and r["price_pcm"] < old["price_pcm"]:
            drops.append(
                f"- {r['area']}: {r['address']} ({r['source']}) {_fmt(old['price_pcm'])} to {_fmt(r['price_pcm'])} {r['url']}"
            )
        if old["status"] != r["status"]:
            status_changes.append(
                f"- {r['area']}: {r['address']} ({r['source']}) {old['status'] or 'unknown'} to {r['status'] or 'unknown'} {r['url']}"
            )
    now_keys = {_row_key(r) for r in now}
    gone = [
        r for r in before.values() if _row_key(r) not in now_keys and r["canonical"]
    ]

    out.append(
        f"## New listings ({len(new)})"
        if prev
        else f"## Listings recorded ({len(canon_now)}, baseline run)"
    )
    out.append("")
    if prev:
        for r in sorted(new, key=lambda r: (r["area"], r["price_pcm"] or 0)):
            commute = (
                f"{r['transit_min']} min"
                if r["transit_min"] is not None
                else "no commute time"
            )
            out.append(
                f"- {r['area']}: {r['address']}, {_fmt(r['price_pcm'])}, {r['sq_ft'] or '?'} sq ft, "
                f"{commute}, {r['status']} ({r['source']}) {r['url']}"
            )
    out += ["", f"## Price drops ({len(drops)})", "", *drops]
    out += ["", f"## Let-agreed changes ({len(status_changes)})", "", *status_changes]
    let_now = sum(r["status"] == LET_AGREED for r in canon_now)
    out += ["", f"Removed since last run: {len(gone)}. Let agreed this run: {let_now}."]
    out += [
        "",
        f"## Median shifts over {SHIFT_THRESHOLD:.0%} ({len(shifts)})",
        "",
        *shifts,
    ]

    gaps = [c for c in coverage_rows(db, run_date) if c["status"] == INCOMPLETE]
    out += ["", f"## Coverage gaps ({len(gaps)} INCOMPLETE searches)", ""]
    for c in gaps:
        out.append(
            f"- {c['source']} / {c['area']} / {c['search']}: displayed {c['displayed']}, collected {c['collected']}. {c['note'] or ''}"
        )

    ons = db.execute(
        "SELECT * FROM ons WHERE month = (SELECT MAX(month) FROM ons) ORDER BY borough"
    ).fetchall()
    if ons:
        out += ["", f"## ONS benchmark, two-bed average rent ({ons[0]['month']})", ""]
        for r in ons:
            out.append(
                f"- {r['borough']}: {_fmt(r['two_bed_pcm'])}, annual change {r['two_bed_annual_change']}%"
            )
    return "\n".join(out) + "\n"


def build(db: sqlite3.Connection, cfg: Config, run_date: str, with_risks: bool) -> str:
    text = facts(db, cfg, run_date)
    if with_risks:
        try:
            text += (
                "\n"
                + write_with_search(cfg.report_model, RISK_INSTRUCTION, text).strip()
                + "\n"
            )
        except LlmError as e:
            text += f"\n## Risks\n\nRisk research failed: {e}\n"
    return text
