"""End-to-end checks against the real services.

    pytest --live      the grounded specialists (Vertex AI only)
    pytest --billed    also a product check and a 2-place survey (Maps Platform billing)

Live data changes over time: a place may close or change its menu. Check
the output before treating a failure as a code bug.
"""

import json
import re

import pytest

from place_assistant.pipeline import product_survey
from place_assistant.sub_agents import maps_lookup_agent, maps_lookup_json_agent, web_search_agent
from place_assistant.tests.live_helpers import run_agent

MAPS_LINK = re.compile(r"https?://(www\.)?(google\.[a-z.]+/maps|maps\.google\.[a-z.]+|maps\.app\.goo\.gl|goo\.gl/maps)", re.I)


def _json_from(text):
    """The JSON object in an answer, ignoring any code fence or text around it."""
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


@pytest.mark.live
def test_maps_lookup_answers_with_facts_and_a_maps_link():
    text = run_agent(maps_lookup_agent, "What's the address and rating of Anomali Coffee Menteng, Jakarta?")["text"]
    assert "menteng" in text.lower()
    assert MAPS_LINK.search(text), f"no Google Maps link in: {text[:400]!r}"


@pytest.mark.live
def test_web_search_cites_sources():
    text = run_agent(web_search_agent, "What are the best rooftop bars in Jakarta?")["text"]
    assert "sources" in text.lower(), f"no Sources section in: {text[-400:]!r}"


@pytest.mark.live
def test_maps_lookup_json_returns_the_schema():
    text = run_agent(maps_lookup_json_agent, "Tell me about Blue Bottle Coffee in Oakland")["text"]
    data = _json_from(text)
    assert data["places"] and data["places"][0]["found"] is True
    assert {"name", "address", "rating", "google_maps_url"} <= set(data["places"][0])


@pytest.mark.live
def test_made_up_place_is_not_invented():
    text = run_agent(maps_lookup_json_agent, "Tell me about Zzyzx Moonbeam Noodle Palace in Menteng, Jakarta")["text"]
    data = _json_from(text)
    assert all(place["found"] is False for place in data["places"]), data


@pytest.mark.billed
def test_named_place_product_check(out_dir):
    # Bintang at Eastern Promise is a verified real case: the pub publishes
    # its beer prices on its own PDF drink list.
    result = product_survey.check_named_places(
        ["Eastern Promise Kemang"], "Jakarta, Indonesia", "Bintang", ["Bir Bintang"], "Indonesian beer brand"
    )
    place = result["places"][0]
    assert place["status"] in ("found_with_price", "found_no_price"), place.get("checks")
    if place["status"] == "found_no_price":
        # The prices are on a slow PDF menu. If it couldn't be read, the
        # result must say so instead of quietly reporting "no price".
        assert "menu not read" in place["checks"]["website"], place["checks"]
    else:
        assert any(e["price"] for e in place["evidence"])
    assert (out_dir / result["json_path"].split("/")[-1]).exists()


@pytest.mark.billed
def test_small_area_survey(out_dir):
    result = product_survey.survey_product(
        "Menteng, Jakarta, Indonesia", "Aqua", ["restaurant"], ["Aqua Reflections"],
        "Danone bottled mineral water brand", limit=2,
    )
    assert result["summary"]["checked"] == 2
    allowed = {"found_with_price", "found_no_price", "mentioned_in_reviews", "no_evidence", "inconclusive"}
    assert {p["status"] for p in result["places"]} <= allowed
    for place in result["places"]:
        for evidence in place["evidence"]:
            if evidence["source"] == "official_website" and evidence["price"]:
                assert evidence["url"], "website prices must say which page they came from"
