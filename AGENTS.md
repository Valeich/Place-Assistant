# Place Assistant: Agent Guide

This file explains every agent in this project: what it does, when it runs, what it returns, and where it falls short. It is written so an AI assistant can explain the system to anyone, from a non-technical stakeholder to a developer, and can change the code safely. People can read it too. Setup and run commands are in [README.md](README.md).

This guide describes the code as of 2026-10-15. If the code and this file disagree, trust the code and update this file.

---

## 1. Summary

Place Assistant is a chat assistant for questions about real places: cafes, bars, restaurants, landmarks. It is built with Google's Agent Development Kit (ADK) and runs on Gemini 3.1 Pro (`gemini-3.1-pro-preview`) through Vertex AI.

One main agent reads each question and hands it to the right specialist:

- a **Google Maps lookup** for facts about named places,
- a **Google Maps lookup that returns JSON**,
- a **Google Search lookup** for recommendations and published information,
- a **bulk collector** that gathers every place of one type in an area and writes a report,
- a **product survey** that checks whether a product, such as Danone's Aqua bottled water, is sold at an area's restaurants or at named places, and at what price. It also reads named restaurants' menus with prices.

Every answer comes from an outside source: Google Maps, Google Search, the Places API, or a business's own website. All agents are instructed never to answer from memory or invent details.

---

## 2. How to explain this project

Match the explanation to the listener.

**Non-technical listener.** Picture a hotel concierge with five specialists on call. The concierge doesn't know the city personally. When you ask something, the concierge decides who to call:

- the **map expert** for the address, rating or hours of a specific place,
- the **data clerk** when you want the same facts in a computer-readable format,
- the **researcher** for "what are the best rooftop bars?" style questions, answered from articles and reviews,
- the **survey team** to list every cafe in a district, check each one, and write a spreadsheet and report,
- the **market researcher** to find out whether a company's product is on the menu at an area's bars or restaurants, at what price, and to bring back the restaurant menus themselves.

The concierge can also open a business's own website to read its menu, and can pull up reports the survey team already wrote.

**Technical listener.** A root ADK `Agent` has eight tools. Five are specialist agents wrapped as `AgentTool`s. Three are plain Python functions. Two specialists use Vertex AI's built-in grounding tools, `google_maps_grounding` and `google_search`. ADK requires each grounding tool to be the only tool on its agent, which is why each sits in its own sub-agent behind an `AgentTool`. The bulk collector and the product survey are deterministic Python pipelines over Places API (New), the Geocoding API, business websites and Search-grounded Gemini calls. The collector also uses Foursquare and OpenStreetMap. Gemini is used inside the pipelines only to read menus and PDFs, confirm product mentions and summarize review sentiment.

**Claims to avoid.** The system can NOT:

- list every place in an area through `web_search`. Only `area_collector` enumerates, and it stops at 60 results per search.
- filter by when a place opened. No Google API exposes an opening date for existing businesses.
- guarantee menus and prices. Menu data is best effort and often second-hand from delivery apps.
- create Google Docs. It writes local `.docx` files that can be opened in Google Docs.
- read Instagram or other login-walled pages.
- give live prices. Prices from delivery apps or blogs may be outdated, and the agents say so.
- prove that a place does NOT sell a product. A `no_evidence` result only means nothing published online that the survey could read mentions it.
- read menus kept on Google Drive, Instagram, delivery apps or link-in-bio pages that block bots. The survey reports such menu links so a person can open them.

---

## 3. Architecture

```
user
 └─ place_assistant  (root agent: routes only, no place knowledge)
     ├─ maps_lookup           AgentTool → Google Maps grounding → Markdown
     ├─ maps_lookup_json      AgentTool → lookup step → JSON format step → JSON
     ├─ web_search            AgentTool → Google Search grounding → Markdown + sources
     ├─ area_collector        AgentTool → collect_places() → pipeline/ → .output/ files
     ├─ product_survey        AgentTool → survey_product_in_area() / check_named_places()
     │                                    → pipeline/product_survey.py → .output/ files
     ├─ fetch_rendered_page   function  → a business's own website (headless Chromium)
     ├─ read_output_file      function  → reads .output/*.json or *.csv
     └─ convert_json_to_docx  function  → .output/*.json → .output/*.docx
```

Facts that shape everything else:

- **Configurable models.** Every agent uses `MODEL`, and the bulk survey steps use `PIPELINE_MODEL`, both in `place_assistant/config.py`. Both default to `gemini-3.1-pro-preview`; set `GEMINI_MODEL` or `GEMINI_PIPELINE_MODEL` in `.env` to change them. Preview models are served only from the `global` location. Survey steps run several times per place, so `GEMINI_PIPELINE_MODEL=gemini-2.5-flash` is the biggest cost and speed saving. Set it: in a live test on 2026-10-15, 3.1 Pro's delivery-app searches cited no sources for 5 of 5 places, so their answers were discarded, while 2.5 Flash cited sources for all 5 and found the product at 2.
- **The root sees only final text.** An `AgentTool` returns a specialist's final answer, not its metadata. So specialists write Maps links and source citations directly into their answer.
- **Tool names are agent names.** The root model calls each specialist by its `name` field (`maps_lookup`, `web_search`, ...). The routing prompt uses the same names, so they must change together.
- **Function docstrings are prompts.** For the three function tools and `collect_places`, ADK sends the Python docstring to the model as the tool description.

### Folder layout

```
place_assistant/                 the only ADK app; `adk web` lists just this folder
├── agent.py                     root agent wiring
├── prompt.py                    root routing rules
├── config.py                    model, API keys, endpoints, output folder
├── sub_agents/
│   ├── maps_lookup/             agent.py, prompt.py
│   ├── maps_lookup_json/        agent.py, prompt.py, schema.py
│   ├── web_search/              agent.py, prompt.py
│   ├── area_collector/          agent.py, prompt.py, tools.py
│   └── product_survey/          agent.py, prompt.py, tools.py
├── tests/                       pytest suite (see section 11)
├── ui/                          the web UI, not an agent
│   ├── server.py                FastAPI: chat, survey and check streams, reports
│   └── static/                  index.html, styles.css, app.js (no build step)
├── tools/
│   ├── web_fetch.py             fetch_rendered_page
│   └── output_files.py          read_output_file, convert_json_to_docx
├── pipeline/                    the batch jobs behind area_collector and product_survey
│   ├── collect.py               orchestrates one collection run
│   ├── product_survey.py        orchestrates one product survey or menu check
│   ├── product_check.py         product evidence for one place
│   ├── geocode.py               area name → bounding box
│   ├── places_client.py         Text Search, Place Details, named-place lookup
│   ├── site_reader.py           a business's website, menu sub-pages, PDF menus
│   ├── menu_extractor.py        menu items and prices
│   ├── prices.py                price text → number and currency
│   ├── foursquare_client.py     extra review text (optional)
│   ├── osm_client.py            OpenStreetMap facilities cross-check
│   ├── sentiment.py             review sentiment summary
│   └── export.py                JSON, CSV and DOCX writers
└── shared/
    ├── llm.py                   one shared Gemini client, 2-minute timeout
    └── web.py                   robots.txt check for every website fetcher
run.py                           run any agent from the terminal
run_collect_places.py            run the collection pipeline directly, no chat
run_product_survey.py            run a product survey or menu check directly
run_ui.py                        start the web UI locally (http://localhost:8080)
.output/                         collection results (git-ignored)
```

---

## 4. Agent reference

### 4.1 `place_assistant`: the root router

| | |
|---|---|
| Files | `place_assistant/agent.py`, `place_assistant/prompt.py` |
| Type | ADK `Agent` |
| Tools | the 5 specialists below plus the 3 function tools |
| Job | Decide which tool or tools answer the request, call them, relay the result |

**Routing rules,** summarized from `prompt.py`:

1. **Use as few tool calls as possible, and chain when needed.** For "recommend a cafe and show its menu": find the place with `web_search` or `maps_lookup`, then ask `product_survey` for that place's menu, then combine. Only when `product_survey` can't run, for example without a Maps API key, read the site with `fetch_rendered_page` and follow 1a to 1c.
   - **1a.** If the user wants website content and `maps_lookup` gave no website, ask `web_search` for the official site and any published menu before giving up.
   - **1b.** If the website shows no itemized prices, ask `web_search` for prices on delivery platforms such as GoFood, GrabFood or DoorDash, or on food blogs. Say plainly that these are third-party prices that may be outdated.
   - **1c.** Quote only text that actually appears in a fetched page. A fetch error means "couldn't retrieve the site", never permission to guess. Instagram-only menus are out of reach.
2. **Relay answers faithfully.** Keep the specialists' Markdown, Maps links and citations. Output `maps_lookup_json`'s JSON exactly, with no code fences, wrapper or extra text.
3. **Report failures plainly,** including the collector's and the survey's billing and API errors.

Product availability, distribution and price questions, and menu requests for named places, go to `product_survey`. Its `no_evidence` status must never be reported as "not sold".
4. **Be concise.**

`maps_lookup_json` is wrapped with `skip_summarization=True`. Without it, the root model rewrote the JSON in its own words, added a wrapper key, and wrote Python `True`/`False` instead of JSON `true`/`false`.

### 4.2 `maps_lookup`: facts about named places

| | |
|---|---|
| Files | `sub_agents/maps_lookup/agent.py`, `prompt.py` |
| Tool | `google_maps_grounding` (must be its only tool) |
| Data | Google Maps' place database through Vertex AI. No Maps API key needed. |
| Router uses it when | the user names one or more specific places, wants facts such as address, hours or rating, or wants a comparison of named places |

**Returns Markdown.** Each place gets a bold heading and bullets for Address, Rating, Total Reviews, Open/Closed Status, Facilities, Price Level and Menu, plus a `[View on Google Maps](url)` link. Comparisons come as a table with columns Name, Address, Rating, Reviews, Status, Price Level, Facilities, Maps Link.

**Limits.** Google Maps has general menu categories but no itemized menus with prices for most businesses, and the agent says so. If a place isn't found, it says so instead of guessing.

### 4.3 `maps_lookup_json`: the same lookup as JSON

| | |
|---|---|
| Files | `sub_agents/maps_lookup_json/agent.py`, `prompt.py`, `schema.py` |
| Type | `SequentialAgent` with two steps |
| Router uses it when | the user explicitly asks for JSON, structured or machine-readable output. Otherwise the router prefers `maps_lookup`. |

**The two steps:**

1. `maps_lookup_step` does the same Maps-grounded lookup as `maps_lookup`. It writes plain text that always includes the Google Maps URL, and stores it in session state as `raw_place_answer`.
2. `json_format_step` has no tools and uses `output_schema`. It only copies facts from that text into the schema.

The split exists because on Gemini 2.5 models, ADK's `output_schema` isn't reliable when the same agent also has tools. Keeping the steps apart works on every model, so the model can be switched freely. The format step includes only places the user asked about, never alternatives the lookup mentions.

**Output schema** (`PlaceDetailsResponse`): `{"places": [ ... ]}`, one entry per place, in the order asked.

| Field | Type | Meaning |
|---|---|---|
| `found` | bool | false if the place couldn't be found |
| `name` | string | the name searched for, even if not found |
| `address` | string or null | full street address |
| `rating` | number or null | stars out of 5 |
| `total_reviews` | int or null | review count |
| `open_now` | bool or null | open right now, if known |
| `price_level` | string or null | e.g. Inexpensive, Moderate, Expensive |
| `facilities` | list of strings | amenity tags such as "outdoor seating" |
| `menu_summary` | string or null | general menu or cuisine description |
| `google_maps_url` | string or null | direct Maps link |

A place that isn't found still gets an entry: `found: false`, `name` set to what was searched, and everything else null or empty.

### 4.4 `web_search`: recommendations and published information

| | |
|---|---|
| Files | `sub_agents/web_search/agent.py`, `prompt.py` |
| Tool | `google_search` (must be its only tool) |
| Data | the live web through Google Search grounding |
| Router uses it when | "best X in Y" or recommendation questions, reviews and roundups, general knowledge about a place or area, and finding a place's official website or second-hand menu prices (rules 1a and 1b) |

**Returns Markdown** with a bold heading per place, then a `Sources:` section listing every source page. The sources go in the answer text because the root can't see grounding metadata.

**Limits.** It reports only what has been published online, so it is not a complete list of an area, and it says so. Sources often show page titles without URLs. Google's Search grounding terms require showing the returned "Search Suggestions" in any end-user product. The agent's `keep_search_suggestions` callback saves them in session state, `AgentTool` passes them up, and the web UI shows them under the answer. The CLI and `adk web` don't show them, which is fine for local testing only.

### 4.5 `area_collector`: bulk collection for an area

| | |
|---|---|
| Files | `sub_agents/area_collector/agent.py`, `prompt.py`, `tools.py`; the pipeline in `pipeline/` |
| Tool | `collect_places(area_name, place_type, limit=20, require_alcohol=False)` |
| Data | Places API (New), Geocoding API, Foursquare (optional), OpenStreetMap, business websites, Google Search |
| Needs | a Maps Platform API key with billing enabled, plus Places API (New) and Geocoding API turned on |
| Router uses it when | the request covers a whole area: "find all cafes in Jakarta Pusat", "bars that serve alcohol around Menteng", "menu and prices for cafes in Menteng". Never for one or two named places. |

**Arguments the agent fills in:**

- `area_name`: the area as the user said it, with city or country added if ambiguous, e.g. "Jakarta Pusat, Indonesia".
- `place_type`: one Google place type inferred from the wording, e.g. "coffee shops" becomes `cafe`, "tourist spots" becomes `tourist_attraction`. If unclear, the agent asks.
- `limit`: how many places to enrich. Each one costs API calls.
- `require_alcohol`: true when the user wants only places serving beer, wine or cocktails.

**Returns on success:**

| Key | Meaning |
|---|---|
| `area`, `place_type`, `require_alcohol` | echo of the request |
| `checked` | places enriched |
| `collected` | places kept after the alcohol filter |
| `capped_at_60` | Google's 60-result search cap was hit, so the area may hold more |
| `json_path`, `csv_path`, `docx_path` | output files in `.output/` |
| `menu_highlights` | per place: `name`, `menu_source`, `menu_note`, up to 5 `items` of `{item, price}`, and `items_truncated` |

`menu_source` is `official_website`, `third_party_search` or `unavailable`. A `third_party_search` result always carries a `menu_note` warning that prices may be outdated.

**Returns on failure:** `{"status": "error", "error_message": "..."}`, e.g. missing API key, billing off, unknown area or invalid place type.

**What the agent tells the user:** checked versus collected, where the files are, whether the 60 cap was hit, menu items with prices exactly as given, the staleness warning beside any third-party prices, and errors word for word. If asked to filter by opening date, it explains that no API offers it.

**Speed and cost.** Every enriched place costs one Place Details call in Google's priciest Places tier, which includes reviews and amenities. It also takes several Gemini calls and reads up to three website pages, including PDF menus. A 2-place test run took about one minute. Larger runs scale roughly linearly.

### 4.6 `product_survey`: product availability, prices and menus

| | |
|---|---|
| Files | `sub_agents/product_survey/agent.py`, `prompt.py`, `tools.py`; the pipeline in `pipeline/product_survey.py`, `product_check.py`, `site_reader.py`, `prices.py` |
| Tools | `survey_product_in_area(area_name, product, place_types, aliases, product_description, limit=20)` and `check_named_places(place_names, area_name, product="", aliases, product_description)` |
| Data | Places API (New), Geocoding API, Google reviews, business websites and PDF menus, and delivery-app and food-blog listings found through Google Search grounding |
| Needs | the same Maps Platform API key as `area_collector` |
| Router uses it when | the user asks whether a product or brand is sold, stocked or distributed at an area's places or at named places, or what it costs there; and for the menu and prices of named places |

**Built for distribution surveys.** A company names its product and an area, or a list of its target outlets. The survey reports which places show the product on a menu or listing, at what price, and where the evidence came from.

**Arguments the agent fills in:**

- `product`: the brand as printed on menus, without category words: "Aqua", not "Aqua mineral water". The code also tries each name without trailing words such as "water", "coffee" or "beer".
- `aliases`: other names of the same product, such as "Aqua Reflections" or "Aqua Botol". Never a competitor. Aliases made only of category words, such as "air mineral" or "mineral water", are dropped because they would match every brand.
- `product_description`: a few words such as "Danone bottled mineral water brand", so unrelated uses of the same word are ignored.
- `place_types`: one to three Google place types, default `["restaurant"]`.
- `limit`: places to check in an area survey, 1 to 60, default 20.
- `place_names`: up to 30 places. Without a product, `check_named_places` returns each place's full menu instead.

**How one place is checked** (`product_check.py`), cheapest source first:

1. **Google reviews,** up to 5 from Place Details.
2. **The place's own website,** its menu sub-pages and PDF menus, through `site_reader.py`.
3. **Delivery apps and food blogs** through a Search-grounded Gemini call. It is skipped when the website already shows the product with a price. An answer that cites no source is discarded.

Each source is searched for the product's names with plain text matching first, so Gemini is only called when a name actually appears. Gemini then decides whether each mention really is the product, for example Aqua the bottled water versus an "aqua blue" cocktail. In Indonesia "aqua" is also an everyday word for any bottled water, so the agent warns that a menu line saying "Aqua" may use it loosely. The code keeps a price only if it appears word for word in the source, and an item only if it names the product and appears in the source.

**Statuses, verdicts and proof per place.** The status is the internal code. The verdict is what people see in the UI, the reports and chat. The proof is one or two sentences built by `pipeline/verdict.py` from that place's own evidence and checks, never by a model, so it can't claim more than the survey saw.

| Status | Verdict | Meaning | Example proof |
|---|---|---|---|
| `found_with_price` | Available | the place's own menu or an online listing shows the product with a price | Listed as "AQUA 600ml" at Rp 8.000 on the place's own menu (satekhas.com). |
| `found_no_price` | Available | listed there, no price shown | Listed as "Aqua" in an online listing (gofood.co.id), without a price. Online listings can be out of date. |
| `mentioned_in_reviews` | Likely available | only a Google review mentions it; weaker evidence | A Google review mentions it: "aqua dingin selalu ready". Reviews can be old. Confirm by phone or visit. |
| `no_evidence` | Needs field check | nothing readable online mentions it yet; NOT proof it isn't sold | Not yet mentioned on its website, in its 5 Google reviews or in online listings such as delivery apps. A call or visit will confirm whether it's stocked. |
| `inconclusive` | Couldn't check fully | a source couldn't be read: an error, an unloadable site, or a menu file such as a Google Drive PDF | Its menu is a linked file or page that can't be opened automatically; the link is in the report. |
| `place_not_found` | Place not found | a named place Google Maps couldn't find | Google Maps has no match for "Warung Xyz" in this area. |
| `not_checked` | Skipped | the run was stopped before this place | The survey was stopped before this place was checked. |
| `menu_found`, `menu_unavailable` | — | menu checks without a product | — |

The proof wording is deliberately neutral: it states what was checked and the next step, not what the tool can't do. Reports and Word files are re-worded with the current verdicts and proofs whenever they are reopened in the UI or rebuilt with `convert_json_to_docx`, and the UI re-reads their prices with the local currency.

Each record also has `checks`, which says per source what happened: `match`, `no_match`, `no_website`, `social_media_only`, `not_allowed` (the website is or redirects to a delivery app or Google page), `blocked_by_robots`, `broken_link`, `fetch_failed` (the site couldn't be reached or loaded, or showed a bot check), `menu_not_readable: <links>`, `no_sources` or `error: ...`. The same item at the same price from one source is kept once.

**Returns** a compact summary to the agent: `summary` (checked, status counts, `price_summary` with currency, count, min, median and max, counting each distinct price once per place and leaving out bundles such as "Steak + Le Minerale", `maps_product_hits`, `skipped_closed`, and `area_note` when the area was widened), a short entry per place, and `json_path`, `csv_path` and `docx_path`. Each found item carries its price as printed and `price_read_as`, for example "60" read as "IDR 60,000". Full evidence with quotes, source URLs, phone numbers and the search's own summary goes to the files. The `.docx` opens with a reading guide, a status table, a price range and an overview table.

**Candidates in an area survey.** Google Maps Text Search is first asked for "`<product> <type>` in `<area>`". Those hits are checked first and flagged `maps_product_match`. That flag only means Google Maps ranked the place for the query; it is not evidence. If there are fewer than `limit`, a plain "`<type>` in `<area>`" search fills the rest. Permanently closed places are skipped.

**Speed and cost.** Places are checked four at a time. Live runs on 2026-10-15 took 35 to 100 seconds for one to three places. Slow websites and large PDF menus dominate. Every place costs one Place Details call in the top Places tier, plus Gemini calls and usually one Search-grounded call.

---

## 5. Function tools on the root agent

### `fetch_rendered_page(url)`: `tools/web_fetch.py`

Opens a business's own website in headless Chromium and returns the visible text, including content drawn by JavaScript.

- **Returns** `{"status": "success", "url", "text", "links"}`. `text` is capped at 15,000 characters. `links` holds up to 40 same-site links as `{text, href}`, so the router can open a sub-page such as "Menu", one or two hops at most.
- **Errors** for a non-http(s) URL, a blocked host, a robots.txt refusal, a failed load, or a page with no visible text.
- **Rules.** It refuses search engines, Google pages and short links, social networks and delivery apps, including when a page redirects there, and keeps only links on the site it actually landed on. It checks robots.txt first, identifies itself with an honest User-Agent, and waits 2.5 seconds for scripts to render. The router may only use it on a real website URL it got from a specialist or the user, never a guessed one.

### `read_output_file(filename)`: `tools/output_files.py`

Shows a collection result in chat, useful in `adk web` where there's no file browser.

- Accepts only `.json` and `.csv` files inside `.output/`, from either the collector or the product survey. Any folder part of the name is stripped, so path tricks can't escape the folder.
- Returns the content, with JSON pretty-printed, and `truncated: true` past 30,000 characters.

### `convert_json_to_docx(filename)`: `tools/output_files.py`

Builds a Word report from an existing JSON result, a collection or a product survey, without re-running the paid job.

- Needed only for runs older than the built-in `.docx` export, or when the `.docx` was deleted.
- Replaces any existing `.docx` with the same name.
- The result is a local file that opens in Google Docs. The project has no Google Docs or Drive API access.

---

## 6. The collection pipeline

`pipeline/collect.py` runs these steps in plain Python, without an LLM deciding each call:

1. **Geocode** the area name to a bounding box with the Geocoding API. An unknown area raises an error. A box under 2 km per side is widened to 2 km around its centre, because neighbourhood names such as "Kemang, Jakarta" often geocode to a box a few hundred metres wide.
2. **Search** with Places Text Search (New) for "`<place type>` in `<area>`", restricted to the box and to `includedType`. It always pages to Google's limit of 60, in 3 pages of 20, so `capped_at_60` is accurate.
3. **Enrich** the first `limit` places, pausing 0.2 seconds between them:
   - **Place Details**: address, rating, reviews, hours, price level, website, amenities, alcohol service.
   - **Foursquare tips**, only when `FOURSQUARE_API_KEY` is set.
   - **Menu**: see below.
   - **Sentiment**: Gemini summarizes Google's up to 5 reviews plus any Foursquare tips, using only that text.
   - **OpenStreetMap**: looks for a name match within 80 metres and copies cuisine, hours, wifi, outdoor seating, wheelchair access, website and phone.
4. **Filter.** With `require_alcohol`, keep only places Google marks with `servesBeer`, `servesWine` or `servesCocktails`. The filter runs after enrichment, so filtered-out places are still billed.
5. **Export** to `.output/<base>.json`, `.csv` and `.docx`.

**Menu extraction** in `menu_extractor.py` tries three things in order:

1. **The place's website,** through `site_reader.py`. It fetches the listed page with a plain HTTP request. Under 200 characters of text means a JavaScript app, so it re-renders the page in headless Chromium. It then follows up to two menu-looking links: same-site pages, PDF menus on any host, and any link from a link-in-bio page. Gemini transcribes PDF menus, including scanned ones. It never fetches Google, social networks or delivery apps, follows redirects one hop at a time so a link that redirects there isn't fetched either, and reports menu links on those hosts instead. Pages that say "404" are treated as broken links, and bot-check pages such as Cloudflare's "Just a moment..." as sites that couldn't be loaded. A site whose robots.txt can't be reached at all counts as down, not as refusing robots. Products are searched in each page's full text; only the menu extraction prompt is capped. Gemini then pulls out only items and prices literally present.
2. **Delivery apps and food blogs.** A Google Search-grounded Gemini call looks for published prices on GoFood, GrabFood, DoorDash, Uber Eats and food blogs. A second Gemini call structures them.
3. **Otherwise** the menu is `unavailable`, and `note` says why each stage found nothing, so a failed fetch no longer looks like "no menu".

The menu record also carries `website_status`, `search_status`, the page URLs read and the search sources cited.

**Output file names** are built from the area and type: lowercased, commas removed, spaces turned into underscores, plus `_alcohol` when filtered. For example, "Jakarta Pusat, Indonesia" with `cafe` and the alcohol filter becomes `jakarta_pusat_indonesia_cafe_alcohol.json`. Running the same area and type again overwrites those files.

**What each file holds:**

- **JSON:** the full records, always written, even when empty.
- **CSV:** the same records flattened, with nested keys joined by dots and lists stored as JSON text. Written only when there are records.
- **DOCX:** a readable report with each place's address, rating, price level, open status, Maps link, alcohol service, a menu table with its source, and the sentiment summary.

### Record fields

| Field | Content |
|---|---|
| `place_id`, `name`, `address` | identity and location |
| `rating`, `total_reviews`, `price_level`, `open_now` | from Place Details; `price_level` uses Google's codes such as `PRICE_LEVEL_MODERATE` |
| `google_maps_url`, `website` | links |
| `facilities` | `outdoor_seating`, `delivery`, `takeout`, `dine_in`, `reservable`, `good_for_children`, `accessibility`, `parking` |
| `alcohol` | `serves_beer`, `serves_wine`, `serves_cocktails`, `serves_alcohol` |
| `reviews_sample` | `google_reviews` (up to 5), `foursquare_tips` |
| `sentiment` | `overall_sentiment` (positive, neutral, negative, mixed or no_data), `summary`, `pros`, `cons` |
| `menu` | `items` as `{item, price}`, `source`, `note` |
| `osm_cross_check` | OpenStreetMap tags, or the text "not found in OpenStreetMap" |

A missing value from Google is `null`, which means "not reported", not "no".

### Web UI

`python run_ui.py` starts a FastAPI server, `place_assistant/ui/server.py`, with a one-page frontend in `ui/static/`. It is a client of the same code the agents use, not a separate system:

| Tab | Calls | Notes |
|---|---|---|
| Assistant | `POST /api/chat` → the root agent through an ADK `Runner` with in-memory sessions | Streams tool calls and answers; history lasts as long as the server process. A tool call cut off by Stop is answered with "stopped by the user" before the next message, because Gemini rejects an unanswered call |
| Product survey | `POST /api/survey` → `pipeline.survey_product` | Runs the pipeline directly, without the LLM router |
| Outlets & menus | `POST /api/check` → `pipeline.check_named_places` | Product check, or full menus without a product |
| Reports | `GET /api/files`, `/api/files/{name}`, `/api/reports/{name}` | Lists, downloads and reopens files in `OUTPUT_DIR` |

Streams are newline-delimited JSON with a ping line every 15 seconds. Survey jobs report progress through the pipeline's `progress` hook, and the server sets the `cancel` event when the browser disconnects or presses Stop. Report names are reduced to a bare file name, so requests can't leave the output folder. The server has no login and binds to localhost by default. It reads settings only from the environment, so it is deployable to any project unchanged, as long as it sits behind authentication. The `Dockerfile` builds it for Cloud Run; README.md, "Deploy to Cloud Run", has the commands.

---

## 7. Example requests and routes

These were run against the live system on 2026-10-11 and 2026-10-15, through the root router unless marked "run directly", which means the specialist or pipeline was called on its own.

| Request | Route | What happens |
|---|---|---|
| "What's the address, rating and opening hours of Anomali Coffee Menteng?" | `maps_lookup` | Grounded facts from Google Maps |
| "Compare Fentons Creamery and Tara's Organic Ice Cream in Oakland" | `maps_lookup`, run directly | Markdown comparison table with Maps links |
| "What are the best rooftop bars in Jakarta?" | `web_search` | List from published articles, with a Sources section |
| "Give me Blue Bottle Coffee in Oakland as JSON" | `maps_lookup_json` | Raw JSON, one entry per branch found |
| "Recommend a work-friendly cafe in Jakarta Pusat and show its menu with prices" | `web_search`, then intended `fetch_rendered_page`, then `web_search` for delivery-app prices | A recommendation plus menu prices labelled as third-party |
| "Collect cafes in Kemang, Jakarta Selatan, limit 2" | `area_collector` | Files written to `.output/`, menu highlights in chat |
| "Show me what's in jakarta_pusat_indonesia_cafe_alcohol.json" | `read_output_file` | File content in chat |
| "Convert menteng_jakarta_indonesia_cafe.json into a Word document" | `convert_json_to_docx` | A new `.docx` path |
| "Fetch the menu from https://www.google.com/maps/place/..." | `maps_lookup`, then `web_search` | Never fetches the Google page; finds menu prices through the specialists instead |
| "Find cafes in Menteng that opened in the last 6 months" | no tool | Explains no API offers opening dates |
| A made-up place | `maps_lookup` or `maps_lookup_json`, run directly | "Not found", or `found: false`; nothing invented |
| "Does Eastern Promise in Kemang, Jakarta sell Bintang beer, and how much does it cost?" | `product_survey`, `check_named_places` | Found on the pub's own website, with prices |
| "Which bars in Kemang, Jakarta sell Bintang beer and at what price? Only check 3 places." | `product_survey`, `survey_product_in_area` | 3 bars checked: one found on a delivery-app listing without a price, two with no evidence, plus the widened-area note |
| "Show me the drinks menu with prices at Eastern Promise in Kemang, Jakarta" | `product_survey`, `check_named_places` without a product | Drinks and prices read from the pub's PDF menu |
| Bintang at "Holywings Gatot Subroto", "Eastern Promise Kemang", "Lucy in the Sky SCBD" | `run_product_survey.py`, run directly | Two found with prices; "Lucy in the Sky SCBD" matched the Senayan Park branch, shown in the result |

---

## 8. Frequently asked questions

**Does it work without the Maps API key?** Yes, except `area_collector` and `product_survey`. Every other agent and tool uses only Vertex AI.

**Does it cost money?** Vertex AI usage, including grounding, bills to the Google Cloud project. `area_collector` and `product_survey` also bill the Maps Platform per place and are the expensive part. Check Google's current pricing pages before estimating.

**Why did it say no menu was found?** Many places list only Instagram as their website, or none. Others keep their menu on Google Drive or a delivery app, which the project doesn't fetch. The delivery-app search also comes up empty for new or small places. The menu's `note`, and the survey's `checks`, say which of these happened.

**Does "no_evidence" ("Needs field check") mean a place doesn't sell our product?** No. It means nothing published online that the survey could read mentions the product. Bars and hotel restaurants often don't publish drink lists at all. Confirm important outlets by phone or visit; phone numbers are in the survey files.

**How much can a "found" result be trusted?** A website or listing result comes with a quote and, for websites, the page URL. Prices are kept only when they appear word for word in the source. Delivery-app and food-blog prices are second-hand and may be outdated. Review mentions are the weakest evidence.

**Why did the survey check a different branch?** A named place is matched to Google Maps' top result for "name, area". The result shows the matched name and address next to the name searched for. Add the street or mall name to the place name to pin the right branch.

**Why does the collector return at most 60 places?** Google's Text Search returns at most 60 results per query. Full coverage of a large district would need the area split into smaller tiles, which isn't built.

**Why did "collected" come back smaller than "checked"?** The alcohol filter removed places Google doesn't mark as serving alcohol. Some bars simply have those fields empty in Google's data, so the filter can under-count.

**Where are my files?** In `.output/` at the project root, no matter which folder the command was run from.

**Why are there two Maps agents?** Same data, different formats. `maps_lookup` writes for people, `maps_lookup_json` writes for programs.

**Why not use the Google Business Profile API?** It only manages listings you own and can't search other businesses. The details are in `GOOGLE_BUSINESS_PROFILE_API_FINDINGS.md`.

---

## 9. Configuration

Settings come from `.env` at the project root. The project has no template file, so create `.env` with these:

| Variable | Used by | Required |
|---|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | all agents | yes, `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | all agents | yes |
| `GOOGLE_CLOUD_LOCATION` | all agents | yes |
| `MAPS_API_KEY` or `GOOGLE_MAPS_API_KEY` | `area_collector`, `product_survey` | for collection and surveys |
| `FOURSQUARE_API_KEY` | collection pipeline | no; tips are skipped without it |
| `GEMINI_MODEL` | all agents | no; defaults to `gemini-3.1-pro-preview` |
| `GEMINI_PIPELINE_MODEL` | survey and collection steps | recommended: `gemini-2.5-flash`; defaults to `GEMINI_MODEL` |
| `MAX_PLACES_PER_RUN` | surveys, outlet checks, area collections | no; defaults to 60. Set it low, e.g. 5, on a demo deployment: it caps every run from the forms and from chat |

Vertex AI authenticates with Application Default Credentials (`gcloud auth application-default login`). Other settings live in `place_assistant/config.py`: the model, API endpoints, the User-Agent, and `OUTPUT_DIR`. The project runs on Python 3.11 and was last tested with google-adk 2.3.0, google-genai 2.10.0 and playwright 1.61.0.

---

## 10. Known issues

**Fixed on 2026-10-15:**

- The Maps API key no longer appears in geocoding error messages.
- The collector now follows menu links and reads PDF menus, instead of reading only a website's homepage.
- A network error in the menu step is now recorded as an error in the menu's `note`, instead of looking like "no menu".
- The robots.txt check had no timeout and could hang a whole run. It now gives up after 10 seconds. Pipeline Gemini calls now time out after 2 minutes.
- An area name containing "/" crashed the save step after every place was billed. File names are now sanitized.
- Neighbourhood names that geocode to a tiny box are widened to 2 km.
- Websites that answer with a "page not found" message are now treated as broken links.
- The first survey after startup could lose a check on up to three places, because parallel workers raced to create the Gemini client. The client is now created once, under a lock.
- Products listed after the first 15,000 characters of a page were missed. The product search now reads the whole page.
- A dead website was reported as "restricts automated access", a bot-check page counted as a read website, and redirects broke menu-link following and could reach blocked hosts. All three are fixed in `site_reader.py` and `fetch_rendered_page`.
- Price summaries counted one price several times and included bundles. An area collection started from chat froze the web UI for its whole run. Stop in chat could leave the conversation unusable. The collector's `limit` wasn't clamped, and an empty re-run left the previous CSV and Word files behind.

**Still open.** Mention these when they're relevant to a question.

1. **One failing place stops a whole collection run.** In `area_collector`, an error in Place Details or the sentiment step aborts the run and writes no files. `product_survey` records such errors per place and carries on.
2. **The router shortens specialist answers.** It often drops the Google Maps link and trims details, such as giving "open until 11 PM" when asked for opening hours. Rule 2 asks it to keep them.
3. **The router tends to skip the business's own website** when it falls back to `fetch_rendered_page`. Named-place menus now go through `product_survey` first, which reads the website itself.
4. **A named place can match a different branch.** "Lucy in the Sky SCBD" matched the Senayan Park branch in testing. The result shows the matched name and address.
5. **Short bare prices pass the word-for-word check easily.** A price like "47" appears in many places on a menu, so the stored quote is the real check.
6. **Multi-column menus can be misread.** Where a menu prints happy-hour and regular prices side by side, the column labels can end up in item names.
7. **Some menus can't be read:** Google Drive files, Instagram, delivery-app pages, and link-in-bio services that block bots, such as Linktree. The survey reports these links instead.
8. **The Search-grounded check sometimes cites no sources.** That answer is discarded and counted as no evidence (`no_sources`).
9. **`web_search` can stray outside the requested area,** e.g. a South Jakarta cafe in a Jakarta Pusat list.
10. **The JSON step sometimes infers values,** such as turning "$10-20" into "Moderate", and can list non-amenities like "breakfast" under facilities.
11. **Some requested Place Details fields aren't saved:** `servesCoffee`, `servesBreakfast`, `servesVegetarianFood`, `restroom` and `menuForChildren`.
12. **The Foursquare endpoints may be outdated.** The code uses the v3 Places API, which Foursquare has been replacing. It fails quietly to an empty list.

---

## 11. Changing the code

| To change... | Edit |
|---|---|
| when the router picks a tool | `place_assistant/prompt.py` |
| a specialist's behavior or answer format | `sub_agents/<name>/prompt.py` |
| a specialist's description shown to the router | the `description` in `sub_agents/<name>/agent.py` |
| the JSON fields | `sub_agents/maps_lookup_json/schema.py`, then the format instruction in its `prompt.py` |
| what the collector tool tells the model | the `collect_places` docstring in `sub_agents/area_collector/tools.py` |
| what the survey tools tell the model | the docstrings in `sub_agents/product_survey/tools.py` |
| how a place is judged to sell a product | `pipeline/product_check.py` |
| survey candidates, statuses and summary | `pipeline/product_survey.py` |
| which website pages and PDFs are read | `pipeline/site_reader.py` |
| how price text becomes a number and currency | `pipeline/prices.py` |
| which data the pipeline gathers | `pipeline/collect.py`, `pipeline/places_client.py` |
| report formats, for collections and surveys | `pipeline/export.py` |
| the model, keys, endpoints, output folder | `place_assistant/config.py` |
| the web UI's API | `place_assistant/ui/server.py` |
| the web UI's pages, labels and layout | `place_assistant/ui/static/` |

**Rules that keep it working:**

- **Keep grounding tools alone.** Never add another tool next to `google_maps_grounding` or `google_search`. Add a new specialist instead.
- **Rename in three places at once:** the agent's `name`, every mention in the root `prompt.py`, and the `AGENTS` table in `run.py`.
- **Treat function docstrings as prompts.** The model reads them, so wording changes behavior.
- **Put new folders inside `place_assistant/`, or give them a dot prefix.** `adk web` treats every other folder at the project root as an agent app and fails on it.
- **Keep facts in the answer text.** A specialist's links and citations must be in its final text, because that's all the root receives.
- **Never invent data.** Every prompt forbids guessing names, prices, ratings or menu items. Keep that rule in new prompts.
- **Run `pytest` after every change.** The offline suite is free and takes about a second. Run `pytest --live` after changing a prompt, a tool docstring or an agent's tools: it checks which tool the model picks for typical questions, with tools stubbed. `pytest --live --billed` runs real surveys of one or two places. Tests live in `place_assistant/tests/`, not at the root, because `adk web` treats root folders as agents.
- **Test single agents with `run.py`:** `python run.py maps "..."`. Avoid testing the collector or the survey with a large `limit`, because every place is billed.
- **Keep the survey honest.** Never let `no_evidence` read as "not sold", keep the third-party staleness warning next to delivery-app prices, and keep prices exactly as printed.
- **Never commit `.env`.** It holds credentials. The hook in `.githooks/pre-commit` blocks it, and blocks Google API keys or private keys in any staged file; enable it with `git config core.hooksPath .githooks`. Older commits in this repository's history still contain a `.env` with a Maps key, so publish a fresh history rather than pushing them.

---

## 12. Glossary

- **ADK (Agent Development Kit):** Google's Python framework for building LLM agents. Provides `Agent`, `SequentialAgent`, `AgentTool`, `adk web` and `adk run`.
- **Agent:** an LLM with an instruction, a description and tools.
- **AgentTool:** wraps an agent so another agent can call it like a function. The caller receives only the final answer.
- **SequentialAgent:** runs sub-agents in a fixed order. Used by `maps_lookup_json`.
- **Grounding:** making the model answer from a live source, Google Maps or Google Search, instead of its training data.
- **Vertex AI:** Google Cloud's AI platform. It runs Gemini and the grounding tools here.
- **Places API (New):** Google's paid API for place data. Text Search finds places, Place Details describes one.
- **Geocoding API:** turns an area name into coordinates and a bounding box.
- **OpenStreetMap and Overpass:** a free, community-built map and its query API, used here as a second opinion on facilities.
- **Foursquare tips:** short user reviews from Foursquare, an optional extra review source.
- **Playwright:** a library that drives a real headless browser, used to read websites built with JavaScript.
- **robots.txt:** a site's file saying what automated tools may fetch. Every website fetcher here obeys it.
- **Link-in-bio page:** a one-page list of links, such as Linktree, that many small businesses give as their website.
- **Soft 404:** a missing page that the server answers with a normal success code and a "page not found" message.
- **Grounding sources:** the web pages a Search-grounded Gemini answer cites. The survey discards an answer that cites none.
- **output_key:** an ADK setting that saves an agent's answer into session state for a later step.
- **skip_summarization:** an `AgentTool` setting that returns a specialist's output untouched instead of letting the caller rewrite it.
