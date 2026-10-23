import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Target:
    beds: int
    baths: int
    max_pcm: int
    area_radius_km: float


@dataclass(frozen=True)
class Destination:
    name: str
    postcode: str
    max_commute_min: int
    arrive_time: str


@dataclass(frozen=True)
class Area:
    name: str
    center: tuple[float, float]
    rightmove: list[str]
    zoopla: list[str]
    openrent: list[str]
    ons_boroughs: list[str]


@dataclass(frozen=True)
class Operator:
    name: str
    urls: list[str]
    developments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    root: Path
    target: Target
    destination: Destination
    extract_model: str
    report_model: str
    chrome: str
    cdp_port: int
    profile_dir: Path
    areas: list[Area]
    operators: list[Operator]

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    root = path.parent.resolve()
    return Config(
        root=root,
        target=Target(**raw["target"]),
        destination=Destination(**raw["destination"]),
        extract_model=raw["models"]["extract"],
        report_model=raw["models"]["report"],
        chrome=raw["browser"]["chrome"],
        cdp_port=raw["browser"]["cdp_port"],
        profile_dir=root / raw["browser"]["profile_dir"],
        areas=[
            Area(
                name=a["name"],
                center=(a["center"][0], a["center"][1]),
                rightmove=a["rightmove"],
                zoopla=a["zoopla"],
                openrent=a["openrent"],
                ons_boroughs=a["ons_boroughs"],
            )
            for a in raw["areas"]
        ],
        operators=[Operator(**o) for o in raw["operators"]],
    )
