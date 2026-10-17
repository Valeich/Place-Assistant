"""Deterministic batch pipelines behind the area_collector and product_survey agents.

    collect.py            orchestrates one run: geocode -> search -> enrich -> export
    geocode.py            area name -> bounding box          (Geocoding API)
    places_client.py      area search + per-place details    (Places API (New))
    foursquare_client.py  extra review text                  (optional)
    osm_client.py         free facilities cross-check        (OpenStreetMap)
    menu_extractor.py     menu items + prices                (own website, then Search)
    site_reader.py        a business's own site, menu sub-pages, PDF menus
    sentiment.py          review sentiment summary           (Gemini)
    export.py             JSON / CSV / DOCX writers
    product_survey.py     is a product sold at an area's places, at what price
    product_check.py      product evidence for one place     (reviews, site, Search)
    verdict.py            plain verdict + one-line proof per surveyed place
    prices.py             price text -> number and currency

It runs in a plain Python loop instead of letting an LLM decide each call:
for dozens of places that is far cheaper and more predictable.
"""

from .collect import collect_places
from .product_survey import check_named_places, survey_product

__all__ = ["check_named_places", "collect_places", "survey_product"]
