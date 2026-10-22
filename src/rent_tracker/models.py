from dataclasses import dataclass, field

AVAILABLE = "available"
LET_AGREED = "let agreed"

OK = "OK"
INCOMPLETE = "INCOMPLETE"


@dataclass
class Listing:
    source: str
    listing_id: str
    area: str
    address: str
    postcode_district: str | None
    price_pcm: int | None
    beds: int | None
    baths: int | None
    sq_ft: int | None
    status: str
    url: str
    lat: float | None = None
    lng: float | None = None
    canonical: bool = True
    other_source_ids: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"


@dataclass
class Coverage:
    source: str
    area: str
    search: str
    displayed: int | None
    collected: int
    status: str
    note: str = ""


def coverage_status(displayed: int | None, collected: int) -> str:
    return OK if displayed is not None and displayed == collected else INCOMPLETE
