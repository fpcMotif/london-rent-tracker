import re

DISTRICT_RE = re.compile(r"\b(([A-Z]{1,2})[0-9][0-9A-Z]?)(?:\s+[0-9][A-Z]{2})?\b")
LONDON_AREAS = {"E", "EC", "N", "NW", "SE", "SW", "W", "WC"}


def postcode_district(text: str) -> str | None:
    found = None
    for m in DISTRICT_RE.finditer(text.upper()):
        if m.group(2) in LONDON_AREAS:
            found = m.group(1)
    return found


def money(text: str) -> int | None:
    m = re.search(r"£\s?([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    return round(float(m.group(1).replace(",", ""))) if m else None


def to_pcm(amount: float, period: str) -> int:
    period = period.lower()
    if period in {"pw", "weekly", "week", "per week"}:
        return round(amount * 52 / 12)
    if period in {"pa", "yearly", "annual", "per annum"}:
        return round(amount / 12)
    return round(amount)


def price_text_pcm(text: str) -> int | None:
    amount = money(text)
    if amount is None:
        return None
    period = (
        "pw" if re.search(r"\bp/?w\b|per week|weekly", text, re.IGNORECASE) else "pcm"
    )
    return to_pcm(amount, period)


def sq_ft(text: str) -> int | None:
    m = re.search(
        r"([0-9][0-9,]*)\s*(sq\.?\s*ft|sqft|square feet|ft²)", text, re.IGNORECASE
    )
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"([0-9][0-9,]*)\s*(sq\.?\s*m|sqm|m²)", text, re.IGNORECASE)
    if m:
        return round(int(m.group(1).replace(",", "")) * 10.7639)
    return None
