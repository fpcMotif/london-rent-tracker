import argparse
import datetime as dt
import json
import sys
from contextlib import nullcontext
from dataclasses import asdict
from functools import partial
from pathlib import Path

from rent_tracker import commute, ons, report, store, workbook
from rent_tracker.browser import CdpChrome
from rent_tracker.config import Config, load_config
from rent_tracker.dedupe import dedupe, haiku_judge
from rent_tracker.geo import districts_for_points
from rent_tracker.models import INCOMPLETE, Coverage, Listing
from rent_tracker.sources import (
    COLLECT_ERRORS,
    RawPages,
    btr,
    http_client,
    http_fetch,
    openrent,
    rightmove,
    zoopla,
)

ALL_SOURCES = ["rightmove", "zoopla", "openrent", "btr"]
CHECKPOINT = "collected.json"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def collect(
    cfg: Config,
    run_date: str,
    sources: list[str],
    areas: list[str] | None,
    replay: bool,
) -> tuple[list[Listing], list[Coverage]]:
    raw_root = cfg.runs_dir / run_date / "raw"
    chosen = [a for a in cfg.areas if not areas or a.name in areas]
    listings: list[Listing] = []
    coverage: list[Coverage] = []

    def add(found: tuple[list[Listing], list[Coverage]], label: str) -> None:
        listings.extend(found[0])
        coverage.extend(found[1])
        flags = sum(c.status == INCOMPLETE for c in found[1])
        log(
            f"  {label}: {len(found[0])} rows, {len(found[1])} searches, {flags} INCOMPLETE"
        )

    def raw_dir(source: str, name: str) -> Path:
        d = raw_root / source / name.replace("/", "-")
        d.mkdir(parents=True, exist_ok=True)
        return d

    with http_client() as client:
        fetch = None if replay else http_fetch(client)
        for area in chosen:
            if "rightmove" in sources:
                pages = RawPages(raw_dir("rightmove", area.name), fetch)
                add(
                    rightmove.collect(pages, area, cfg.target),
                    f"rightmove {area.name}",
                )
            if "openrent" in sources:
                pages = RawPages(raw_dir("openrent", area.name), fetch)
                add(
                    openrent.collect(pages, area, cfg.target),
                    f"openrent {area.name}",
                )
        need_browser = not replay and ("zoopla" in sources or "btr" in sources)
        with (
            CdpChrome(cfg.chrome, cfg.cdp_port, cfg.profile_dir)
            if need_browser
            else nullcontext(None)
        ) as browser:
            if "zoopla" in sources:
                fetch_zoopla = (
                    None if browser is None else partial(zoopla.fetch_page, browser)
                )
                for area in chosen:
                    pages = RawPages(raw_dir("zoopla", area.name), fetch_zoopla)
                    add(zoopla.collect(pages, area, cfg.target), f"zoopla {area.name}")
            if "btr" in sources:
                for op in cfg.operators:
                    d = raw_dir("btr", op.name)
                    extract = (
                        btr.saved_extractor(cfg.extract_model, d)
                        if browser is None
                        else btr.live_extractor(browser, cfg.extract_model, d)
                    )
                    add(
                        btr.collect(extract, client, op, chosen, cfg.target),
                        f"btr {op.name}",
                    )

        unique: dict[str, Listing] = {}
        for x in listings:
            if x.price_pcm is None or x.price_pcm > cfg.target.max_pcm:
                continue
            if x.beds != cfg.target.beds or x.baths != cfg.target.baths:
                continue
            unique.setdefault(x.key, x)
        listings = list(unique.values())

        missing = [
            (x, (x.lat, x.lng))
            for x in listings
            if not x.postcode_district and x.lat is not None and x.lng is not None
        ]
        if missing:
            districts = districts_for_points(client, [point for _, point in missing])
            for (x, _), district in zip(missing, districts, strict=True):
                x.postcode_district = district
    return listings, coverage


def save_checkpoint(
    path: Path, listings: list[Listing], coverage: list[Coverage]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "listings": [asdict(x) for x in listings],
        "coverage": [asdict(c) for c in coverage],
    }
    path.write_text(json.dumps(data, indent=1))


def load_checkpoint(path: Path) -> tuple[list[Listing], list[Coverage]]:
    data = json.loads(path.read_text())
    return (
        [Listing(**x) for x in data["listings"]],
        [Coverage(**c) for c in data["coverage"]],
    )


def cmd_run(cfg: Config, args: argparse.Namespace) -> None:
    run_date = args.date or dt.date.today().isoformat()
    db = store.connect(cfg.data_dir / "tracker.db")
    checkpoint = cfg.runs_dir / run_date / CHECKPOINT
    if args.resume:
        listings, coverage = load_checkpoint(checkpoint)
        log(f"resume {run_date}: {len(listings)} rows from {checkpoint}")
    else:
        replay = args.command == "reparse"
        log(
            f"{args.command} {run_date}: sources {', '.join(args.sources)}"
            + (", from saved pages" if replay else "")
        )
        listings, coverage = collect(cfg, run_date, args.sources, args.area, replay)
        if args.area or set(args.sources) != set(ALL_SOURCES):
            kept_rows, kept_cov = store.load_run(db, run_date)

            def replaced(source: str, area: str) -> bool:
                family = source.split(":")[0]
                return family in args.sources and (
                    not args.area or area in args.area or family == "btr"
                )

            listings += [x for x in kept_rows if not replaced(x.source, x.area)]
            coverage += [c for c in kept_cov if not replaced(c.source, c.area)]
            log(f"kept stored rows for the other sources and areas of {run_date}")
        save_checkpoint(checkpoint, listings, coverage)
        log(f"checkpoint {checkpoint}")
    log(f"deduplicating {len(listings)} rows")
    judge = None if args.no_llm_dedupe else haiku_judge(cfg.extract_model, log)
    dedupe(listings, judge)
    store.save_run(db, run_date, listings, coverage)
    finish(cfg, db, run_date, args.no_risks)


def finish(cfg: Config, db, run_date: str, no_risks: bool) -> None:
    with http_client() as client:
        try:
            done, failed = commute.refresh(db, client, cfg, dt.date.today(), log)
            log(f"commute: {done} new points timed, {failed} failed")
        except (*COLLECT_ERRORS, commute.TflError) as e:
            log(f"commute refresh failed: {e}")
        try:
            log(f"ons: {ons.refresh(db, client, cfg)}")
        except COLLECT_ERRORS as e:
            log(f"ons refresh failed: {e}")
    xlsx = cfg.data_dir / "rent-tracker.xlsx"
    workbook.build(db, cfg, xlsx)
    text = report.build(db, cfg, run_date, with_risks=not no_risks)
    out = cfg.runs_dir / run_date / "report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    log(f"workbook {xlsx}\nreport {out}")


def cmd_report(cfg: Config, args: argparse.Namespace) -> None:
    db = store.connect(cfg.data_dir / "tracker.db")
    run_date = args.date or store.run_dates(db)[-1]
    finish(cfg, db, run_date, args.no_risks)


def main() -> None:
    parser = argparse.ArgumentParser(prog="rent-tracker")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser(
        "run", help="collect every source, then rebuild the workbook and report"
    )
    reparse = sub.add_parser(
        "reparse",
        help="rebuild a run from its saved raw pages without fetching listing pages",
    )
    for p in (run, reparse):
        p.add_argument("--date", help="run date, YYYY-MM-DD (default today)")
        p.add_argument("--sources", nargs="+", choices=ALL_SOURCES, default=ALL_SOURCES)
        p.add_argument(
            "--area", action="append", help="limit to one area name; repeatable"
        )
        p.add_argument(
            "--no-risks",
            action="store_true",
            help="skip the web-searched risks section",
        )
        p.add_argument(
            "--no-llm-dedupe",
            action="store_true",
            help="leave unclear duplicate pairs unmerged",
        )
    run.add_argument(
        "--resume",
        action="store_true",
        help=f"skip collection and continue from runs/<date>/{CHECKPOINT}",
    )
    reparse.set_defaults(resume=False)
    rep = sub.add_parser(
        "report", help="rebuild the workbook and report for a stored run"
    )
    rep.add_argument("--date")
    rep.add_argument("--no-risks", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    {"run": cmd_run, "reparse": cmd_run, "report": cmd_report}[args.command](cfg, args)
