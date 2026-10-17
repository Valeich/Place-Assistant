"""The area collector pipeline, the menu extractor, and Word report conversion."""

import asyncio
import json

import pytest
from docx import Document

from place_assistant.pipeline import collect, export, menu_extractor, site_reader
from place_assistant.sub_agents.area_collector.tools import collect_places as collect_tool
from place_assistant.tests.conftest import gemini_response


def _no_site(url):
    return {"status": "no_website", "pages": [], "error": None, "menu_links_not_read": []}


def test_menu_from_the_website(monkeypatch, fake_gemini):
    page = {"status": "ok", "pages": [{"url": "https://a.com/menu", "kind": "html", "text": "Kopi Susu 25k"}],
            "error": None, "menu_links_not_read": []}
    monkeypatch.setattr(site_reader, "read_business_site", lambda url: page)
    fake = fake_gemini(lambda p, g: json.dumps([{"item": "Kopi Susu", "price": "25k"}]), menu_extractor)
    menu = menu_extractor.extract_menu("https://a.com/", "Cafe A", "Menteng")
    assert menu["source"] == "official_website" and menu["source_urls"] == ["https://a.com/menu"]
    assert not any(c["grounded"] for c in fake.calls)  # no paid search needed


def test_menu_from_search_keeps_its_sources_and_warning(monkeypatch, fake_gemini):
    monkeypatch.setattr(site_reader, "read_business_site", _no_site)

    def handler(prompt, grounded):
        if grounded:
            return gemini_response("GoFood: Kopi Susu Rp 25.000", ["gofood.co.id"])
        return json.dumps([{"item": "Kopi Susu", "price": "Rp 25.000"}])

    fake_gemini(handler, menu_extractor)
    menu = menu_extractor.extract_menu(None, "Cafe A", "Menteng")
    assert menu["source"] == "third_party_search" and "outdated" in menu["note"]
    assert menu["sources"][0]["title"] == "gofood.co.id"


def test_menu_unavailable_says_why(monkeypatch, fake_gemini):
    monkeypatch.setattr(site_reader, "read_business_site", _no_site)
    fake_gemini(lambda p, g: gemini_response("Nothing found", sources=()), menu_extractor)
    menu = menu_extractor.extract_menu(None, "Cafe A", "Menteng")
    assert menu["source"] == "unavailable"
    assert menu["note"] == "Website: no_website. Delivery/blog search: no_sources."


def test_collector_writes_reports_and_menu_highlights(monkeypatch, maps_key, out_dir):
    details = {"A": {"displayName": {"text": "Cafe A"}, "servesBeer": True}, "B": {"displayName": {"text": "Cafe B"}}}
    menu = {"items": [{"item": f"Item {i}", "price": "10k"} for i in range(7)], "source": "official_website", "note": None}
    monkeypatch.setattr(collect.geocode, "geocode_area_bounds", lambda area: {})
    monkeypatch.setattr(collect.places_client, "search_places_text", lambda *a, **k: [{"id": "A"}, {"id": "B"}])
    monkeypatch.setattr(collect.places_client, "get_place_details", lambda pid: details[pid])
    monkeypatch.setattr(collect.foursquare_client, "get_tips", lambda *a: [])
    monkeypatch.setattr(collect.menu_extractor, "extract_menu", lambda *a: menu)
    monkeypatch.setattr(collect.sentiment, "summarize_sentiment", lambda *a: {"overall_sentiment": "no_data", "summary": ""})
    monkeypatch.setattr(collect.osm_client, "get_osm_tags", lambda *a: {})
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)

    result = asyncio.run(collect_tool("Menteng / Jakarta", "cafe", limit=2, require_alcohol=True))
    assert result["checked"] == 2 and result["collected"] == 1  # alcohol filter
    assert result["json_path"].endswith("menteng___jakarta_cafe_alcohol.json")
    highlight = result["menu_highlights"][0]
    assert len(highlight["items"]) == 5 and highlight["items_truncated"] is True
    assert "records" not in result  # full records go to files, not chat


def test_collector_errors_become_messages(monkeypatch, maps_key):
    monkeypatch.setattr(collect.geocode, "geocode_area_bounds", lambda area: (_ for _ in ()).throw(ValueError("Could not geocode 'Atlantis'")))
    assert asyncio.run(collect_tool("Atlantis", "cafe")) == {"status": "error", "error_message": "Could not geocode 'Atlantis'"}


def test_convert_json_to_docx_handles_surveys_and_collections(out_dir, monkeypatch):
    import place_assistant.tools.output_files as output_files

    monkeypatch.setattr(output_files, "_OUTPUT_DIR", str(out_dir.resolve()))
    out_dir.mkdir()
    survey = {"summary": {"kind": "menu_check", "status_counts": {"menu_found": 1}, "place_names": ["A"]},
              "places": [{"name": "A", "status": "menu_found", "menu": {"items": [{"item": "Kopi", "price": "25"}], "source": "official_website"}}]}
    (out_dir / "survey.json").write_text(json.dumps(survey))
    (out_dir / "collection.json").write_text(json.dumps([{"name": "Cafe A", "menu": {"items": []}}]))
    for name in ("survey.json", "collection.json"):
        result = output_files.convert_json_to_docx(name)
        assert result["status"] == "success"
        assert Document(result["docx_path"]).paragraphs  # opens as a Word document


def test_empty_survey_writes_json_only(out_dir):
    paths = export.save_survey({"summary": {}, "places": []}, out_dir, "empty", "Empty")
    assert paths["csv_path"] is None and paths["docx_path"] is None
    assert json.loads(open(paths["json_path"]).read()) == {"summary": {}, "places": []}


@pytest.mark.parametrize("limit, enriched", [(-1, 1), (0, 1), (2, 2), (999, 3)])
def test_collector_limit_is_clamped(monkeypatch, maps_key, out_dir, limit, enriched):
    # A negative or zero limit must never enrich (and bill) the whole area.
    fetched = []
    monkeypatch.setattr(collect.geocode, "geocode_area_bounds", lambda area: {})
    monkeypatch.setattr(collect.places_client, "search_places_text", lambda *a, **k: [{"id": i} for i in "ABC"])
    monkeypatch.setattr(collect, "_enrich_place", lambda pid, area: fetched.append(pid) or {"name": pid, "alcohol": {}})
    monkeypatch.setattr(collect.export, "save_docx", lambda *a, **k: None)
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    collect.collect_places("Menteng", "cafe", limit=limit)
    assert len(fetched) == enriched


def test_an_empty_rerun_removes_the_older_csv_and_word_files(out_dir):
    place = {"name": "A", "status": "no_evidence"}
    first = export.save_survey({"summary": {"kind": "product_survey"}, "places": [place]}, out_dir, "run", "Run")
    assert first["csv_path"] and first["docx_path"]
    export.save_survey({"summary": {"kind": "product_survey"}, "places": []}, out_dir, "run", "Run")
    assert sorted(p.name for p in out_dir.iterdir()) == ["run.json"]

    export.save_records([{"name": "A"}], out_dir, "cafes")
    export.save_docx([{"name": "A"}], out_dir, "cafes", "Cafes")
    export.save_records([], out_dir, "cafes")
    export.save_docx([], out_dir, "cafes", "Cafes")
    assert sorted(p.name for p in out_dir.iterdir()) == ["cafes.json", "run.json"]


def test_a_deployment_cap_limits_the_collector(monkeypatch, maps_key, out_dir):
    from place_assistant import config

    monkeypatch.setattr(config, "MAX_PLACES_PER_RUN", 2)
    fetched = []
    monkeypatch.setattr(collect.geocode, "geocode_area_bounds", lambda area: {})
    monkeypatch.setattr(collect.places_client, "search_places_text", lambda *a, **k: [{"id": i} for i in "ABC"])
    monkeypatch.setattr(collect, "_enrich_place", lambda pid, area: fetched.append(pid) or {"name": pid, "alcohol": {}})
    monkeypatch.setattr(collect.export, "save_docx", lambda *a, **k: None)
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    result = collect.collect_places("Menteng", "cafe", limit=10)
    assert fetched == ["A", "B"] and "at most 2 places per run" in result["limit_note"]
