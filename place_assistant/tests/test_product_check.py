"""Product evidence for one place (pipeline/product_check.py).

Gemini is faked, so these tests check the code's own guards: what it sends
to Gemini, and what it refuses to keep from Gemini's answer.
"""

import json

import pytest

from place_assistant.pipeline import product_check, site_reader
from place_assistant.pipeline.product_check import ProductSpec

BINTANG = ProductSpec("Bintang", ("Bir Bintang", "Bintang Pilsener"), "Indonesian beer brand")


# --- Names and snippets ---------------------------------------------------------

def test_terms_add_core_names_without_category_words():
    assert ProductSpec("Bintang beer", ("Bir Bintang",)).terms == ("Bintang beer", "Bir Bintang", "Bintang")
    assert ProductSpec("Aqua mineral water").terms == ("Aqua mineral water", "Aqua")
    assert ProductSpec("Teh Botol Sosro", ("Teh Botol",)).terms == ("Teh Botol Sosro", "Teh Botol")


AQUA = ProductSpec("Aqua", ("Aqua Reflections",), "Danone bottled mineral water brand")


def test_generic_aliases_are_dropped_but_the_product_is_kept():
    spec = ProductSpec("Aqua", ("Air Mineral", "mineral water", "Aqua Reflections", "AQUA botol"))
    assert spec.terms == ("Aqua Reflections", "AQUA botol", "Aqua")
    assert ProductSpec("Mineral Water").terms == ("Mineral Water",)  # the user's own product name stays
    assert ProductSpec("Teh Botol Sosro", ("Teh Botol",)).terms[-1] == "Teh Botol"  # a brand, not generic


@pytest.mark.parametrize(
    "text, found",
    [
        ("AQUA 600ml Rp 8.000", True),
        ("Air mineral Aqua botol 6k", True),
        ("Aquarium Bar & Grill", False),
        ("Aquafina 600ml 10k", False),  # a competitor sharing the letters
        ("Air Mineral Rp 6.000", False),  # generic water, no brand
    ],
)
def test_aqua_matching(text, found):
    assert bool(product_check.find_snippets(text, AQUA)) is found


def test_terms_drop_duplicates_and_blanks():
    assert ProductSpec("Bintang", ("bintang", " ", "")).terms == ("Bintang",)


def test_short_product_names():
    assert ProductSpec("XO", ("XO",)).terms == ("XO",)  # a 2-letter brand is still searched
    assert ProductSpec("A").terms == ()
    # No terms must match nothing, not every position in the text.
    assert product_check.find_snippets("Heineken 45k, Guinness 60k", ProductSpec("A")) == []


@pytest.mark.parametrize(
    "text, found",
    [
        ("BEERS | BINTANG HALF PINT | 47", True),
        ("Bir  Bintang 330ml Rp 35.000", True),  # extra spaces still match
        ("Bintangan cake", False),  # whole words only
        ("Heineken 45k, Guinness 60k", False),
    ],
)
def test_find_snippets_whole_words(text, found):
    assert bool(product_check.find_snippets(text, BINTANG)) is found


def test_find_snippets_merges_nearby_matches():
    text = "Bintang 35k " + "x" * 50 + " Bintang Radler 38k"
    assert len(product_check.find_snippets(text, BINTANG)) == 1


# --- Gemini confirmation guards ---------------------------------------------------

def _answer(*matches):
    return lambda prompt, grounded: json.dumps({"matches": list(matches)})


def test_confirm_keeps_grounded_items_and_prices(fake_gemini):
    fake_gemini(_answer({"snippet_id": 0, "item": "Bir Bintang 330ml", "price": "Rp 35.000"}), product_check)
    result = product_check._confirm(BINTANG, ["Bir Bintang 330ml Rp 35.000"], "menu")
    assert result == [{"snippet": "Bir Bintang 330ml Rp 35.000", "item": "Bir Bintang 330ml", "price": "Rp 35.000"}]


def test_confirm_trims_punctuation_from_items(fake_gemini):
    fake_gemini(_answer({"snippet_id": 0, "item": "Aqua,", "price": "7.000"}), product_check)
    assert product_check._confirm(AQUA, ["Teh Manis 5.000, Aqua, 7.000"], "menu")[0]["item"] == "Aqua"


def test_confirm_drops_items_that_dont_name_the_product(fake_gemini):
    fake_gemini(_answer({"snippet_id": 0, "item": "Heineken", "price": "Rp 45.000"}), product_check)
    assert product_check._confirm(BINTANG, ["Bintang Rp 35.000 Heineken Rp 45.000"], "menu") == []


def test_confirm_drops_prices_not_in_the_source(fake_gemini):
    fake_gemini(_answer({"snippet_id": 0, "item": "Bintang", "price": "Rp 99.000"}), product_check)
    result = product_check._confirm(BINTANG, ["Bintang Rp 35.000"], "menu")
    assert result[0]["price"] == ""


def test_confirm_drops_invented_items_without_a_verbatim_price(fake_gemini):
    # "Bintang Crystal" is not in the text and its price is invented: dropped.
    fake_gemini(_answer({"snippet_id": 0, "item": "Bintang Crystal", "price": "Rp 99.000"}), product_check)
    assert product_check._confirm(BINTANG, ["Bintang Rp 35.000"], "menu") == []


def test_confirm_keeps_reworded_items_with_a_verbatim_price(fake_gemini):
    fake_gemini(_answer({"snippet_id": 0, "item": "Bintang Large", "price": "55k"}), product_check)
    result = product_check._confirm(BINTANG, ["BINTANG\nSmall 35k\nLarge 55k"], "menu")
    assert result == [{"snippet": "BINTANG\nSmall 35k\nLarge 55k", "item": "Bintang Large", "price": "55k"}]


def test_confirm_ignores_bad_snippet_ids_and_bad_json(fake_gemini):
    fake_gemini(_answer({"snippet_id": 7, "item": "Bintang", "price": ""}), product_check)
    assert product_check._confirm(BINTANG, ["Bintang"], "menu") == []
    fake_gemini(lambda prompt, grounded: "not json", product_check)
    assert product_check._confirm(BINTANG, ["Bintang"], "menu") == []


def test_confirm_prompt_names_the_product_and_its_meaning(fake_gemini):
    fake = fake_gemini(_answer(), product_check)
    product_check._confirm(BINTANG, ["Pelayanan bintang 5"], "review")
    prompt = fake.calls[0]["prompt"]
    assert "Indonesian beer brand" in prompt and "Bir Bintang" in prompt and "[0]" in prompt


# --- Status ---------------------------------------------------------------------------

def _ev(source, price=None):
    return {"source": source, "price": price}


@pytest.mark.parametrize(
    "evidence, checks, status",
    [
        ([_ev("official_website", "35k")], {}, "found_with_price"),
        ([_ev("third_party_search", "35k")], {}, "found_with_price"),
        ([_ev("third_party_search")], {}, "found_no_price"),
        ([_ev("google_reviews", "35k")], {}, "mentioned_in_reviews"),
        ([], {"website": "no_website", "third_party_search": "no_match"}, "no_evidence"),
        ([], {"website": "social_media_only"}, "no_evidence"),
        ([], {"website": "fetch_failed"}, "inconclusive"),
        ([], {"website": "menu_not_readable: https://drive.google.com/x"}, "inconclusive"),
        ([], {"third_party_search": "error: TimeoutError"}, "inconclusive"),
    ],
)
def test_classify(evidence, checks, status):
    assert product_check.classify(evidence, checks) == status


# --- One place, end to end ----------------------------------------------------------------

def _site(status="ok", text="", links_not_read=()):
    pages = [{"url": "https://bar.example/menu", "kind": "html", "text": text}] if status == "ok" else []
    return {"status": status, "pages": pages, "error": None, "menu_links_not_read": list(links_not_read)}


def _gemini_for_place(listing_text="NOT FOUND", sources=("gofood.co.id",)):
    from place_assistant.tests.conftest import gemini_response

    def handler(prompt, grounded):
        if grounded:
            return gemini_response(listing_text, sources)
        matches = []
        for line in prompt.split("\n["):
            idx = line.split("]")[0]
            if not idx.isdigit():
                continue
            if "Rp 35.000" in line:
                matches.append({"snippet_id": int(idx), "item": "Bintang", "price": "Rp 35.000"})
            if "IDR 40K" in line:
                matches.append({"snippet_id": int(idx), "item": "Bintang Draught", "price": "IDR 40K"})
        return json.dumps({"matches": matches})

    return handler


PLACE = {"name": "Bar X", "address": "Jl. Kemang Raya, Jakarta, Indonesia", "website": "https://bar.example/", "reviews": []}


def test_website_price_skips_the_paid_search(monkeypatch, fake_gemini):
    monkeypatch.setattr(site_reader, "read_business_site", lambda url: _site(text="Beers: Bintang Rp 35.000"))
    fake = fake_gemini(_gemini_for_place(), product_check)
    result = product_check.check_product(PLACE, BINTANG, "Kemang, Jakarta, Indonesia")
    assert result["status"] == "found_with_price"
    assert result["evidence"][0]["price_value"] == 35000.0 and result["evidence"][0]["currency"] == "IDR"
    assert result["checks"]["third_party_search"].startswith("skipped")
    assert not any(call["grounded"] for call in fake.calls)


def test_third_party_listing_is_used_when_the_site_has_nothing(monkeypatch, fake_gemini):
    monkeypatch.setattr(site_reader, "read_business_site", lambda url: _site("social_media_only"))
    fake_gemini(_gemini_for_place("GrabFood lists Bintang Draught for IDR 40K."), product_check)
    result = product_check.check_product(PLACE, BINTANG, "Kemang")
    assert result["status"] == "found_with_price"
    assert result["evidence"][0]["source"] == "third_party_search"
    assert result["third_party_sources"][0]["title"] == "gofood.co.id"


def test_search_answer_without_sources_is_discarded(monkeypatch, fake_gemini):
    monkeypatch.setattr(site_reader, "read_business_site", lambda url: _site("no_website"))
    fake_gemini(_gemini_for_place("Bintang Draught IDR 40K", sources=()), product_check)
    result = product_check.check_product(PLACE, BINTANG, "Kemang")
    assert result["status"] == "no_evidence"
    assert result["checks"]["third_party_search"] == "no_sources"


def test_one_failing_source_makes_the_place_inconclusive_not_crash(monkeypatch, fake_gemini):
    def broken(url):
        raise ConnectionError("network down")

    monkeypatch.setattr(site_reader, "read_business_site", broken)
    fake_gemini(_gemini_for_place(), product_check)
    result = product_check.check_product(PLACE, BINTANG, "Kemang")
    assert result["status"] == "inconclusive"
    assert result["checks"]["website"].startswith("error: ConnectionError")
    assert result["checks"]["third_party_search"] == "no_match"  # the other sources still ran


def test_listing_without_price_points_to_the_unread_menu(monkeypatch, fake_gemini):
    monkeypatch.setattr(
        site_reader, "read_business_site",
        lambda url: _site(text="Beers on tap: Bintang Draught", links_not_read=["https://bar.example/drinks.pdf"]),
    )

    def handler(prompt, grounded):
        return json.dumps({"matches": [{"snippet_id": 0, "item": "Bintang Draught", "price": ""}]})

    fake_gemini(handler, product_check)
    result = product_check.check_product(PLACE, BINTANG, "Kemang", search_third_party=False)
    assert result["status"] == "found_no_price"
    assert result["checks"]["website"] == "match (menu not read: https://bar.example/drinks.pdf)"


def test_unreadable_menu_link_is_reported(monkeypatch, fake_gemini):
    monkeypatch.setattr(
        site_reader, "read_business_site",
        lambda url: _site(text="Welcome", links_not_read=["https://drive.google.com/file/d/1/view"]),
    )
    fake_gemini(_gemini_for_place(), product_check)
    result = product_check.check_product(PLACE, BINTANG, "Kemang", search_third_party=False)
    assert result["status"] == "inconclusive"
    assert result["checks"]["website"] == "menu_not_readable: https://drive.google.com/file/d/1/view"


def test_the_same_item_and_price_counts_once(monkeypatch, fake_gemini):
    # "7.000" and "7.000 IDR" are one price; the same line on two pages is one item.
    site = _site(text="Aqua 7.000 | Aqua 7.000 IDR")
    site["pages"].append({"url": "https://bar.example/menu", "kind": "html", "text": "Aqua 7.000"})
    monkeypatch.setattr(site_reader, "read_business_site", lambda url: site)

    def handler(prompt, grounded):
        return json.dumps({"matches": [{"snippet_id": 0, "item": "Aqua", "price": "7.000"},
                                       {"snippet_id": 0, "item": "Aqua", "price": "7.000 IDR"}]})

    fake_gemini(handler, product_check)
    result = product_check.check_product(PLACE, AQUA, "Kemang", search_third_party=False)
    assert [(e["item"], e["price"]) for e in result["evidence"]] == [("Aqua", "7.000")]
