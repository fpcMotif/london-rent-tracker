import re
from collections.abc import Callable
from itertools import combinations

from rent_tracker.geo import km_between
from rent_tracker.models import Listing

SOURCE_RANK = {"rightmove": 0, "zoopla": 1, "openrent": 2}
NEAR_KM = 0.1
STOP_WORDS = {
    "london",
    "flat",
    "apartment",
    "apartments",
    "house",
    "road",
    "street",
    "lane",
    "the",
    "and",
    "of",
    "to",
    "bed",
    "bedroom",
    "rent",
    "city",
    "east",
    "west",
    "north",
    "south",
    "greater",
}

PAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "same": {"type": "array", "items": {"type": "boolean"}},
    },
    "required": ["same"],
}
PAIR_INSTRUCTION = (
    "Stdin lists numbered pairs of rental listings from different websites. Both listings in a pair have the same "
    "price and compatible size. For each pair, answer true only when the addresses clearly name the same building "
    "or street, so they are the same flat. Answer false when unsure. Return one boolean per pair, in order."
)

Judge = Callable[[list[tuple[Listing, Listing]]], list[bool]]


def tokens(address: str) -> set[str]:
    words = re.findall(r"[a-z]+", address.lower())
    return {w for w in words if len(w) > 2 and w not in STOP_WORDS}


def size_compatible(a: Listing, b: Listing) -> bool:
    if a.sq_ft is None or b.sq_ft is None:
        return True
    return abs(a.sq_ft - b.sq_ft) <= 0.03 * max(a.sq_ft, b.sq_ft)


def distance_km(a: Listing, b: Listing) -> float | None:
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return None
    return km_between((a.lat, a.lng), (b.lat, b.lng))


def classify(a: Listing, b: Listing) -> str:
    if (
        a.source == b.source
        or a.price_pcm is None
        or a.price_pcm != b.price_pcm
        or not size_compatible(a, b)
    ):
        return "different"
    if (
        a.postcode_district
        and b.postcode_district
        and a.postcode_district != b.postcode_district
    ):
        return "different"
    near = distance_km(a, b)
    if tokens(a.address) & tokens(b.address) or (near is not None and near <= NEAR_KM):
        return "same"
    return "unclear"


def rank(x: Listing) -> tuple[int, str]:
    return SOURCE_RANK.get(x.source, 3), x.listing_id


def dedupe(listings: list[Listing], judge: Judge | None = None) -> list[Listing]:
    parent = list(range(len(listings)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    unclear = []
    for i, j in combinations(range(len(listings)), 2):
        verdict = classify(listings[i], listings[j])
        if verdict == "same":
            parent[find(i)] = find(j)
        elif verdict == "unclear":
            unclear.append((i, j))
    if unclear and judge:
        answers = judge([(listings[i], listings[j]) for i, j in unclear])
        for (i, j), same in zip(unclear, answers):
            if same:
                parent[find(i)] = find(j)

    groups: dict[int, list[Listing]] = {}
    for i, x in enumerate(listings):
        groups.setdefault(find(i), []).append(x)
    for members in groups.values():
        members.sort(key=rank)
        head, rest = members[0], members[1:]
        head.canonical = True
        head.other_source_ids = [x.key for x in rest]
        for x in rest:
            x.canonical = False
            x.other_source_ids = [head.key]
    return listings


def haiku_judge(model: str, warn: Callable[[str], None]) -> Judge:
    from rent_tracker.llm import LlmError, structured

    def judge(pairs: list[tuple[Listing, Listing]]) -> list[bool]:
        answers: list[bool] = []
        for start in range(0, len(pairs), 50):
            chunk = pairs[start : start + 50]
            lines = [
                f"{n}. A [{a.source}] {a.address} | {a.sq_ft or '?'} sq ft || B [{b.source}] {b.address} | {b.sq_ft or '?'} sq ft"
                for n, (a, b) in enumerate(chunk, 1)
            ]
            try:
                answer = structured(
                    model, PAIR_INSTRUCTION, "\n".join(lines), PAIR_SCHEMA
                )["same"]
            except LlmError as e:
                warn(
                    f"duplicate check failed for {len(chunk)} pairs, left unmerged: {e}"
                )
                answer = []
            answers += answer if len(answer) == len(chunk) else [False] * len(chunk)
        return answers

    return judge
