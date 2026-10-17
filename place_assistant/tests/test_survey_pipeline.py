"""Product surveys and named-place checks (pipeline/product_survey.py), with
Google Maps faked and the per-place product check stubbed."""

import json
import threading

import pytest
from docx import Document

from place_assistant.pipeline import geocode, menu_extractor, places_client, product_check, product_survey


@pytest.fixture
def maps(monkeypatch, maps_key, out_dir):
    """Fakes geocoding and Places. Returns a dict the test can adjust."""
    state = {
        "bounds": {"northeast": {"lat": 1, "lng": 1}, "southwest": {"lat": 0, "lng": 0}},
        "product_hits": [],
        "type_hits": [],
        "fail_details": set(),
        "details_calls": [],
    }

    def search(query, bounds, included_type, max_results):
        hits = state["product_hits"] if query.lower().startswith("bintang") else state["type_hits"]
        return [{"id": h[0], "displayName": {"text": h[1]}, "formattedAddress": f"{h[1]} street, Jakarta, Indonesia",
                 **({"businessStatus": h[2]} if len(h) > 2 else {})} for h in hits][:max_results]

    def details(place_id, field_mask=None):
        state["details_calls"].append(place_id)
        if place_id in state["fail_details"]:
            raise RuntimeError("503 Service Unavailable")
        return {"id": place_id, "displayName": {"text": f"Bar {place_id}"},
                "formattedAddress": "Jl. Kemang, Jakarta, Indonesia", "nationalPhoneNumber": "0812",
                "reviews": [{"text": {"text": "Great place"}}]}

    monkeypatch.setattr(geocode, "geocode_area_bounds", lambda area: state["bounds"])
    monkeypatch.setattr(places_client, "search_places_text", search)
    monkeypatch.setattr(places_client, "get_place_details", details)
    monkeypatch.setattr(places_client, "find_place",
                        lambda name, area: None if "nowhere" in name.lower() else {"id": name.split()[0], "displayName": {"text": name}})
    return state


def _found(place, spec, area, third=True):
    """A stub product check: places whose id starts with F sell the product."""
    if place["place_id"].startswith("F"):
        return {"status": "found_with_price",
                "evidence": [{"source": "official_website", "item": "Bintang", "price": "Rp 35.000",
                              "price_value": 35000.0, "currency": "IDR", "quote": "Bintang Rp 35.000", "url": None}],
                "checks": {"website": "match"}, "third_party_sources": [], "third_party_summary": ""}
    return {"status": "no_evidence", "evidence": [], "checks": {"website": "no_website"},
            "third_party_sources": [], "third_party_summary": ""}


@pytest.fixture
def stub_check(monkeypatch):
    monkeypatch.setattr(product_check, "check_product", _found)


def test_area_survey(maps, stub_check, out_dir):
    maps["product_hits"] = [("F1", "Alpha"), ("C1", "Closed bar", "CLOSED_PERMANENTLY")]
    maps["type_hits"] = [("F1", "Alpha"), ("N1", "Beta"), ("N2", "Gamma"), ("N3", "Delta")]
    result = product_survey.survey_product("Kemang", "Bintang", ["bar"], ["Bir Bintang"], "beer", limit=3)

    s = result["summary"]
    assert [p["place_id"] for p in result["places"]] == ["F1", "N1", "N2"]  # Maps product hits first
    assert [p["maps_product_match"] for p in result["places"]] == [True, False, False]
    assert s["checked"] == 3 and s["found"] == 1 and s["skipped_closed"] == 1 and s["maps_product_hits"] == 1
    assert s["status_counts"]["found_with_price"] == 1 and s["status_counts"]["no_evidence"] == 2
    assert s["price_summary"]["min"] == 35000.0 and s["aliases"] == ["Bir Bintang"]
    assert "C1" not in maps["details_calls"]  # closed places are never billed
    assert result["places"][0]["verdict"] == "Available" and result["places"][0]["proof"].startswith('Listed as "Bintang"')
    assert result["places"][1]["verdict"] == "Needs field check" and "A call or visit will confirm" in result["places"][1]["proof"]

    for kind in ("json", "csv", "docx"):
        assert (out_dir / f"kemang_product_bintang_bar.{kind}").exists()
    saved = json.loads((out_dir / "kemang_product_bintang_bar.json").read_text())
    assert saved["summary"]["kind"] == "product_survey"
    text = "\n".join(p.text for p in Document(out_dir / "kemang_product_bintang_bar.docx").paragraphs)
    assert "Needs field check" in text and "Not confirmed" not in text


def test_one_failing_place_doesnt_stop_the_survey(maps, stub_check):
    maps["type_hits"] = [("N1", "Beta"), ("N2", "Gamma")]
    maps["fail_details"] = {"N1"}
    result = product_survey.survey_product("Kemang", "Bintang", ["bar"], limit=2)
    first, second = result["places"]
    assert first["status"] == "inconclusive" and first["name"] == "Beta"  # name kept from the search
    assert first["checks"]["place_details"].startswith("error: RuntimeError")
    assert second["status"] == "no_evidence"


def test_widened_area_is_noted(maps, stub_check):
    maps["bounds"] = {**maps["bounds"], "widened": True}
    maps["type_hits"] = [("N1", "Beta")]
    assert "widened" in product_survey.survey_product("Kemang", "Bintang", limit=1)["summary"]["area_note"]


def test_progress_events(maps, stub_check):
    maps["type_hits"] = [("N1", "Beta"), ("F1", "Alpha")]
    events = []
    product_survey.survey_product("Kemang", "Bintang", limit=2, progress=events.append)
    stages = [e["stage"] for e in events]
    assert stages[:3] == ["geocoding", "searching", "checking"] and stages[-1] == "saving"
    done = [e for e in events if e["stage"] == "place_done"]
    assert sorted(e["index"] for e in done) == [0, 1] and done[-1]["done"] == 2 == done[-1]["total"]


def test_stop_skips_places_not_started(maps, monkeypatch):
    maps["type_hits"] = [(f"N{i}", f"Bar {i}") for i in range(10)]
    cancel = threading.Event()

    def check(place, spec, area, third=True):
        cancel.set()  # the user presses Stop while the first places run
        return _found(place, spec, area)

    monkeypatch.setattr(product_check, "check_product", check)
    result = product_survey.survey_product("Kemang", "Bintang", limit=10, cancel=cancel)
    statuses = [p["status"] for p in result["places"]]
    assert result["summary"]["stopped_early"] is True
    assert statuses.count("not_checked") >= 10 - product_survey._WORKERS
    assert result["summary"]["checked"] == statuses.count("no_evidence")
    assert len(maps["details_calls"]) <= product_survey._WORKERS


def test_progress_listener_errors_are_ignored(maps, stub_check):
    maps["type_hits"] = [("N1", "Beta")]

    def broken(event):
        raise ValueError("UI went away")

    assert product_survey.survey_product("Kemang", "Bintang", limit=1, progress=broken)["summary"]["checked"] == 1


def test_inputs_are_validated(maps):
    with pytest.raises(ValueError, match="product name"):
        product_survey.survey_product("Kemang", "  ")
    with pytest.raises(ValueError, match="At least one"):
        product_survey.check_named_places([], "Jakarta", "Bintang")
    with pytest.raises(ValueError, match="At most 30"):
        product_survey.check_named_places([f"P{i}" for i in range(31)], "Jakarta", "Bintang")


def test_named_places_product_check(maps, stub_check):
    result = product_survey.check_named_places(["F1 Alpha", "N1 Beta", "Nowhere Bar"], "Jakarta", "Bintang")
    statuses = {p["searched_name"]: p["status"] for p in result["places"]}
    assert statuses == {"F1 Alpha": "found_with_price", "N1 Beta": "no_evidence", "Nowhere Bar": "place_not_found"}
    assert result["places"][2]["proof"].startswith('Google Maps has no match for "Nowhere Bar"')
    assert result["summary"]["checked"] == 2 and result["summary"]["place_names"][2] == "Nowhere Bar"


def test_named_places_full_menus(maps, monkeypatch):
    menu = {"items": [{"item": "Kopi Susu", "price": "25"}], "source": "official_website", "note": None}
    monkeypatch.setattr(menu_extractor, "extract_menu", lambda website, name, area: menu)
    result = product_survey.check_named_places(["A1 Anomali"], "Jakarta")
    assert result["summary"]["kind"] == "menu_check"
    assert result["places"][0]["status"] == "menu_found" and result["places"][0]["menu"] == menu


def test_a_closed_place_found_by_several_searches_counts_once(maps, stub_check):
    maps["product_hits"] = [("C1", "Closed bar", "CLOSED_PERMANENTLY")]
    maps["type_hits"] = [("C1", "Closed bar", "CLOSED_PERMANENTLY"), ("N1", "Beta")]
    result = product_survey.survey_product("Kemang", "Bintang", ["bar", "pub"], limit=5)
    assert result["summary"]["skipped_closed"] == 1


def test_a_one_letter_product_is_rejected(maps):
    with pytest.raises(ValueError, match="at least 2 characters"):
        product_survey.survey_product("Kemang", "A")


def test_a_deployment_cap_limits_places_per_run(maps, stub_check, monkeypatch):
    from place_assistant import config

    monkeypatch.setattr(config, "MAX_PLACES_PER_RUN", 2)
    maps["type_hits"] = [(f"N{i}", f"Bar {i}") for i in range(5)]
    result = product_survey.survey_product("Kemang", "Bintang", limit=5)
    assert result["summary"]["checked"] == 2 and len(maps["details_calls"]) == 2
    assert "at most 2 places per run" in result["summary"]["limit_note"]
    with pytest.raises(ValueError, match="at most 2 places per run"):
        product_survey.check_named_places(["A1 a", "B1 b", "C1 c"], "Jakarta", "Bintang")
    assert "limit_note" not in product_survey.survey_product("Kemang", "Bintang", limit=2)["summary"]
