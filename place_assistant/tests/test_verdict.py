"""Plain verdicts and proof sentences for surveyed places (pipeline/verdict.py)."""

import pytest

from place_assistant.pipeline.verdict import explain


def _ev(source, item, price=None, url=None, quote=None):
    return {"source": source, "item": item, "price": price, "url": url, "quote": quote}


def test_available_from_the_own_menu_quotes_item_price_and_site():
    record = {"status": "found_with_price", "website": "https://www.satekhas.com/",
              "evidence": [_ev("official_website", "AQUA 600ml", "Rp 8.000", "https://www.satekhas.com/menu"),
                           _ev("official_website", "Aqua Reflections", "Rp 15.000")]}
    assert explain(record) == {
        "verdict": "Available",
        "proof": 'Listed as "AQUA 600ml" at Rp 8.000 on the place\'s own menu (satekhas.com). 1 more matching item in the report.',
    }


def test_available_from_a_listing_names_the_site_and_warns():
    record = {"status": "found_with_price", "evidence": [_ev("third_party_search", "Aqua Botol", "Rp 10.000")],
              "third_party_sources": [{"title": "gofood.co.id"}, {"title": "gofood.co.id"}, {"title": "instagram.com"}]}
    proof = explain(record)["proof"]
    assert proof == 'Listed as "Aqua Botol" at Rp 10.000 in an online listing (gofood.co.id, instagram.com). Online listings can be out of date.'


def test_own_menu_price_is_preferred_over_a_listing():
    record = {"status": "found_with_price", "website": "https://a.id",
              "evidence": [_ev("third_party_search", "Aqua", "Rp 9.000"), _ev("official_website", "Aqua", "Rp 8.000", "https://a.id/m")]}
    assert "Rp 8.000 on the place's own menu" in explain(record)["proof"]


def test_available_without_price_points_to_the_unread_menu():
    record = {"status": "found_no_price", "website": "https://bar.id", "evidence": [_ev("official_website", "Aqua", url="https://bar.id/")],
              "checks": {"website": "match (menu not read: https://drive.google.com/x)"}}
    proof = explain(record)["proof"]
    assert proof.startswith('Listed as "Aqua" on the place\'s own menu (bar.id), without a price.')
    assert "may show the price" in proof


def test_review_only_is_likely_available_with_a_quote():
    record = {"status": "mentioned_in_reviews", "evidence": [_ev("google_reviews", "aqua", quote="… aqua dingin selalu ready …")]}
    out = explain(record)
    assert out["verdict"] == "Likely available" and '"aqua dingin selalu ready"' in out["proof"]


def test_needs_field_check_says_what_was_checked_and_the_next_step():
    record = {"status": "no_evidence", "reviews_checked": 5,
              "checks": {"google_reviews": "no_match", "website": "no_match (2 page(s) read)", "third_party_search": "no_match"}}
    out = explain(record)
    assert out["verdict"] == "Needs field check"
    assert out["proof"] == (
        "Not yet mentioned on its website, in its 5 Google reviews or in online listings such as delivery apps. "
        "A call or visit will confirm whether it's stocked."
    )


@pytest.mark.parametrize(
    "website, note",
    [
        ("no_website", "It lists no website."),
        ("social_media_only", "It uses a social media page as its website."),
        ("blocked_by_robots", "Its website restricts automated access."),
        ("broken_link", "Its website link is broken."),
    ],
)
def test_needs_field_check_explains_why_the_website_wasnt_read(website, note):
    record = {"status": "no_evidence", "reviews_checked": 1, "checks": {"website": website, "third_party_search": "no_sources"}}
    proof = explain(record)["proof"]
    assert proof.startswith("Not yet mentioned in its 1 Google review or in online listings") and note in proof


def test_couldnt_check_fully_gives_the_reason():
    assert "can't be opened automatically" in explain({"status": "inconclusive", "checks": {"website": "menu_not_readable: https://x"}})["proof"]
    assert "couldn't be loaded" in explain({"status": "inconclusive", "checks": {"website": "fetch_failed"}})["proof"]
    assert "Google Maps details" in explain({"status": "inconclusive", "checks": {"place_details": "error: 503"}})["proof"]
    assert "third party search check failed" in explain({"status": "inconclusive", "checks": {"third_party_search": "error: x"}})["proof"]


def test_place_not_found_and_skipped():
    assert explain({"status": "place_not_found", "searched_name": "Warung Xyz"})["proof"].startswith('Google Maps has no match for "Warung Xyz"')
    assert explain({"status": "not_checked"}) == {"verdict": "Skipped", "proof": "The survey was stopped before this place was checked."}


def test_needs_field_check_when_the_website_is_a_delivery_app():
    record = {"status": "no_evidence", "reviews_checked": 0, "checks": {"website": "not_allowed", "third_party_search": "no_match"}}
    assert "Its website link leads to a delivery app or Google page." in explain(record)["proof"]


def test_a_bundle_price_is_not_the_proof_when_the_product_has_its_own():
    record = {"status": "found_with_price", "evidence": [
        _ev("third_party_search", "1 Black Angus Sirloin Steak 200 gr + 1 Le Minerale", "Rp. 219.000++"),
        _ev("third_party_search", "LE MINERALE", "22.000"),
    ], "third_party_sources": [{"title": "gofood.co.id"}]}
    assert explain(record)["proof"].startswith('Listed as "LE MINERALE" at 22.000')
