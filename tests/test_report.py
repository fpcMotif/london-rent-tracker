from pathlib import Path

from rent_tracker import report, store
from rent_tracker.config import load_config
from rent_tracker.models import AVAILABLE, Listing

CONFIG = load_config(Path(__file__).parents[1] / "config.toml")


def test_report_credits_tfl_as_its_licence_requires(tmp_path: Path) -> None:
    db = store.connect(tmp_path / "t.db")
    flat = Listing(
        "rightmove",
        "1",
        "Stratford/East Village",
        "x",
        "E15",
        2500,
        2,
        2,
        None,
        AVAILABLE,
        "u",
        51.54,
        -0.003,
    )
    store.save_run(db, "2026-10-22", [flat], [])
    assert "Powered by TfL Open Data" in report.facts(db, CONFIG, "2026-10-22")
