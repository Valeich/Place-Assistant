"""Price text -> number and currency (pipeline/prices.py)."""

import pytest

from place_assistant.pipeline import prices


@pytest.mark.parametrize(
    "text, default, expected",
    [
        ("Rp 25.000", None, (25000.0, "IDR")),
        ("Rp. 1.250.000", None, (1250000.0, "IDR")),
        ("Rp35.000,-", None, (35000.0, "IDR")),
        ("IDR 25K", None, (25000.0, "IDR")),
        ("IDR 45", None, (45000.0, "IDR")),  # rupiah under 1,000 means thousands
        ("IDR 45.5K", None, (45500.0, "IDR")),
        ("35rb", None, (35000.0, "IDR")),
        ("25-30k", None, (25000.0, None)),  # a range gives its lower bound
        ("2 for 50k", None, (50000.0, None)),  # quantities are skipped
        ("Bintang 330ml 35k", None, (35000.0, None)),  # sizes are skipped
        ("2 botol 15rb", None, (15000.0, "IDR")),  # so are bottle counts
        ("$4.50", None, (4.5, "USD")),
        ("S$ 12", None, (12.0, "SGD")),
        ("RM12.90", None, (12.9, "MYR")),
        ("€ 9,50", None, (9.5, "EUR")),
        ("1,500.50", None, (1500.5, None)),
        ("25.000,00", None, (25000.0, None)),
        ("47", "IDR", (47000.0, "IDR")),  # bare menu number in an Indonesian place
        ("4.50", "USD", (4.5, "USD")),
        ("$5", "IDR", (5.0, "USD")),  # an explicit currency beats the default
        ("free", None, (None, None)),
        ("", None, (None, None)),
    ],
)
def test_parse_price(text, default, expected):
    assert prices.parse_price(text, default) == expected


@pytest.mark.parametrize(
    "address, currency",
    [
        ("Jl. Kemang Raya No.5, Jakarta 12720, Indonesia", "IDR"),
        ("Menteng, Jakarta, Indonesia", "IDR"),
        ("Oakland, CA, USA", "USD"),
        ("Orchard Rd, Singapore", "SGD"),
        ("Nowhere", None),
        (None, None),
    ],
)
def test_currency_for_address(address, currency):
    assert prices.currency_for_address(address) == currency


def test_summarize_prices_uses_the_most_common_currency():
    entries = [
        {"price_value": 25000, "currency": "IDR"},
        {"price_value": 35000, "currency": "IDR"},
        {"price_value": 30000, "currency": "IDR"},
        {"price_value": 4.5, "currency": "USD"},
        {"price_value": None, "currency": None},
    ]
    assert prices.summarize_prices(entries) == {
        "currency": "IDR",
        "count": 3,
        "min": 25000,
        "median": 30000,
        "max": 35000,
        "other_currencies_ignored": 1,
    }


def test_summarize_prices_without_prices():
    assert prices.summarize_prices([{"price_value": None}]) is None


def test_format_read_as():
    assert prices.format_read_as(60000.0, "IDR") == "IDR 60,000"
    assert prices.format_read_as(4.5, "USD") == "USD 4.50"
    assert prices.format_read_as(None, "IDR") is None


def test_survey_price_entries_count_each_price_once_per_place_and_skip_bundles():
    def ev(item, price, value, source="third_party_search"):
        return {"source": source, "item": item, "price": price, "price_value": value, "currency": "IDR"}

    places = [
        {"evidence": [ev("Aqua Dingin", "7.000", 7000.0), ev("Aqua", "7.000 IDR", 7000.0),
                      ev("Aqua 1500ml", "12.000", 12000.0)]},
        {"evidence": [ev("LE MINERALE", "22.000", 22000.0),
                      ev("1 Black Angus Sirloin Steak 200 gr + 1 Le Minerale", "Rp. 219.000++", 219000.0),
                      ev("Paket Hemat Le Minerale", "35.000", 35000.0),
                      ev("Le Minerale", None, None, source="official_website"),
                      ev("Le Minerale", "20.000", 20000.0, source="google_reviews")]},
        {"evidence": [ev("Aqua", "7.000", 7000.0)]},  # the same price at another place counts
    ]
    assert [e["price_value"] for e in prices.survey_price_entries(places)] == [7000.0, 12000.0, 22000.0, 7000.0]
    assert prices.is_bundle("Nasi Goreng + Aqua") and not prices.is_bundle("Aqua Reflections 380ml")
