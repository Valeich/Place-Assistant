"""The product_survey agent's tools: what the model receives back."""

import asyncio

from place_assistant.pipeline import product_survey
from place_assistant.sub_agents.product_survey import tools


def _record(name, status, evidence=(), **extra):
    return {"name": name, "address": "Jakarta, Indonesia", "status": status, "evidence": list(evidence),
            "checks": {"website": "no_website"}, "third_party_sources": [], **extra}


def _ev(source, item, price=None, value=None, currency=None):
    return {"source": source, "item": item, "price": price, "price_value": value, "currency": currency,
            "quote": f"... {item} ...", "url": None}


def test_product_results_are_compact_and_keep_caveats(monkeypatch):
    evidence = [_ev("google_reviews", "bintang"), _ev("third_party_search", "Bintang Draught", "40", 40000.0, "IDR"),
                *[_ev("official_website", f"Bintang {i}", f"{50 + i}", 50000.0 + i, "IDR") for i in range(4)]]
    result = {"summary": {"kind": "product_survey"}, "json_path": "/x/s.json", "csv_path": None, "docx_path": None,
              "places": [_record("A", "found_with_price", evidence, maps_product_match=True),
                         _record("B", "no_evidence", searched_name="Bee Bar")]}
    monkeypatch.setattr(product_survey, "survey_product", lambda *a, **k: result)

    out = asyncio.run(tools.survey_product_in_area("Kemang", "Bintang"))
    a, b = out["places"]
    assert out["status"] == "success" and len(a["found"]) == 3 and a["more_found_in_files"] == 3
    assert all(f["source"] != "google_reviews" for f in a["found"])  # menu evidence shown first
    third = next((f for f in a["found"] if f["source"] == "third_party_search"), None)
    assert third is None or "outdated" in third["note"]
    assert a["found"][0]["price_read_as"].startswith("IDR ")
    assert "checks" not in a and b["checks"] == {"website": "no_website"}  # reasons only when nothing was found
    assert b["searched_name"] == "Bee Bar"
    assert "verdict" in a and "proof" in a  # the agent repeats these as written


def test_tool_errors_become_messages(monkeypatch):
    def failing(*a, **k):
        raise RuntimeError("Could not geocode 'Atlantis': ZERO_RESULTS")

    monkeypatch.setattr(product_survey, "check_named_places", failing)
    out = asyncio.run(tools.check_named_places(["X"], "Atlantis"))
    assert out == {"status": "error", "error_message": "Could not geocode 'Atlantis': ZERO_RESULTS"}


def test_menu_results_include_read_as_prices(monkeypatch):
    record = _record("Eastern Promise", "menu_found", address="Jl. Kemang Raya, Jakarta, Indonesia",
                     menu={"items": [{"item": f"Beer {i}", "price": "53"} for i in range(30)],
                           "source": "official_website", "note": None})
    result = {"summary": {"kind": "menu_check"}, "places": [record], "json_path": "/x/m.json"}
    monkeypatch.setattr(product_survey, "check_named_places", lambda *a, **k: result)
    place = asyncio.run(tools.check_named_places(["Eastern Promise"], "Jakarta"))["places"][0]
    assert len(place["items"]) == 25 and place["items_truncated"] is True
    assert place["items"][0]["price_read_as"] == "IDR 53,000"
