"""Turns menu price text such as "Rp 25.000", "IDR 25K", "35rb" or "$4.50"
into a number and a currency, and summarizes prices across places.

Best effort only. The original price text is always kept next to the number,
and text that can't be read confidently becomes None instead of a guess.
"""

import re
import statistics
from collections import Counter

# Checked in order, so "S$" is read as SGD before the plain "$" rule.
_CURRENCIES = [
    ("IDR", re.compile(r"\brp\.?|\bidr\b", re.I)),
    ("SGD", re.compile(r"\bs\$|\bsgd\b", re.I)),
    ("MYR", re.compile(r"\brm\s?\d|\bmyr\b", re.I)),
    ("USD", re.compile(r"\busd\b|\bus\$|(?<![a-z])\$", re.I)),
    ("EUR", re.compile(r"€|\beur\b", re.I)),
    ("GBP", re.compile(r"£|\bgbp\b", re.I)),
]

_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")

# "25k", "25 rb", "25-30k": a suffix that multiplies the number.
_MULTIPLIER = re.compile(r"^\s*(?:[-–~]\s*[\d.,]+\s*)?(k|rb|ribu|jt|juta)\b", re.I)

# A number followed by one of these is a quantity or size, not a price:
# "2 for 50k", "330ml 35k", "2 pcs", "2 botol 15rb".
_NOT_A_PRICE = re.compile(
    r"^\s*(for\b|x\b|pcs?\b|ml\b|cl\b|l\b|oz\b|gr?\b|kg\b|%|"
    r"botol\b|btl\b|bottles?\b|cans?\b|kaleng\b|gelas\b|glass(es)?\b|pack\b|porsi\b)",
    re.I,
)

# Bundle items such as "Steak 200gr + 1 Le Minerale" or "Paket Hemat": their
# price is for the whole set, not the product.
_BUNDLE = re.compile(r"\+|\b(paket|combo|bundle|package|set menu)\b", re.I)


def _to_float(token: str) -> float | None:
    """Reads "25.000", "1,500.50", "25.000,00" or "4.50" as a number."""
    dots, commas = token.count("."), token.count(",")
    if dots and commas:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        token = token.replace(thousands, "").replace(decimal, ".")
    elif dots or commas:
        sep = "." if dots else ","
        groups = token.split(sep)
        if len(groups) > 2 or len(groups[-1]) == 3:
            token = token.replace(sep, "")  # thousands separators
        else:
            token = token.replace(sep, ".")  # decimal mark
    try:
        return float(token)
    except ValueError:
        return None


def detect_currency(text: str) -> str | None:
    """Returns a currency code such as "IDR" or "USD" found in text, or None."""
    for code, pattern in _CURRENCIES:
        if pattern.search(text or ""):
            return code
    return None


# Country, as the last part of a Google formatted address -> local currency.
_COUNTRY_CURRENCY = {
    "indonesia": "IDR", "singapore": "SGD", "malaysia": "MYR", "thailand": "THB",
    "philippines": "PHP", "vietnam": "VND", "japan": "JPY", "australia": "AUD",
    "india": "INR", "united states": "USD", "usa": "USD", "united kingdom": "GBP",
    "uk": "GBP", "germany": "EUR", "france": "EUR", "netherlands": "EUR",
    "italy": "EUR", "spain": "EUR", "belgium": "EUR", "ireland": "EUR",
}


def currency_for_address(address: str | None) -> str | None:
    """Guesses the local currency from the country at the end of an address
    or area name, e.g. "..., Jakarta 10350, Indonesia" -> "IDR".
    """
    country = (address or "").rsplit(",", 1)[-1].strip().lower()
    return _COUNTRY_CURRENCY.get(country)


def parse_price(text: str, default_currency: str | None = None) -> tuple[float | None, str | None]:
    """Returns (value, currency) for a price string, e.g. "IDR 25K" gives
    (25000.0, "IDR"). Ranges such as "25-30k" give the lower bound, and
    rupiah amounts under 1,000 are read as thousands. default_currency is
    used when the text names none, as on menus that print just "47". Either
    part is None when it can't be read.
    """
    if not text:
        return None, None
    currency = detect_currency(text)
    match = next(
        (m for m in _NUMBER.finditer(text) if not _NOT_A_PRICE.match(text[m.end():])),
        None,
    )
    if not match:
        return None, currency
    value = _to_float(match.group())
    suffix = _MULTIPLIER.match(text[match.end():])
    if value is not None and suffix:
        value *= 1_000_000 if suffix.group(1).lower() in ("jt", "juta") else 1_000
    if currency is None and suffix and suffix.group(1).lower() in ("rb", "ribu", "jt", "juta"):
        currency = "IDR"  # "rb"/"ribu"/"jt" are Indonesian shorthand
    currency = currency or default_currency
    if currency == "IDR" and value is not None and value < 1_000:
        # No rupiah menu price is under 1,000, so "IDR 45" means 45,000.
        value *= 1_000
    return value, currency


def format_read_as(value: float | None, currency: str | None) -> str | None:
    """How a parsed price reads in full, e.g. (60000.0, "IDR") -> "IDR 60,000".
    None when there is no value.
    """
    if not value:
        return None
    amount = f"{value:,.0f}" if value >= 100 else f"{value:,.2f}"
    return f"{currency} {amount}" if currency else amount


def is_bundle(item: str | None) -> bool:
    """True for a menu item that bundles the product with something else."""
    return bool(_BUNDLE.search(item or ""))


def survey_price_entries(places: list[dict]) -> list[dict]:
    """The menu and listing prices of a product survey that its price
    summary counts: each distinct price once per place, so one price seen on
    several pages or listings isn't counted several times, and no bundle
    prices, which are for more than the product.
    """
    entries = []
    for place in places:
        seen = set()
        for e in place.get("evidence") or []:
            if e.get("source") not in ("official_website", "third_party_search") or not e.get("price"):
                continue
            key = (e.get("price_value"), e.get("currency"))
            if is_bundle(e.get("item")) or key in seen:
                continue
            seen.add(key)
            entries.append(e)
    return entries


def summarize_prices(entries: list[dict]) -> dict | None:
    """Summarizes entries that carry "price_value" and "currency".

    Uses the most common currency only, so a stray "$" price never mixes
    with rupiah. Returns None when no entry has a readable price.
    """
    priced = [e for e in entries if e.get("price_value")]
    if not priced:
        return None
    currency = Counter(e.get("currency") for e in priced).most_common(1)[0][0]
    values = [e["price_value"] for e in priced if e.get("currency") == currency]
    return {
        "currency": currency,
        "count": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "other_currencies_ignored": len(priced) - len(values),
    }
