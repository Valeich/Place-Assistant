# Place Assistant

A Google ADK agent that answers questions about real places: cafes, bars, restaurants, landmarks. One root agent reads each request and hands it to the right specialist. Every specialist is grounded in Google data, so answers come from Maps, Search, or a business's own website, never from the model's memory.

For a detailed guide to every agent, written so AI assistants can explain and maintain the project, see [AGENTS.md](AGENTS.md).

## Folder map

```
place-assistant/
├── place_assistant/            the ADK app (the only folder `adk web` shows)
│   ├── agent.py                root agent: routes each request to a specialist or tool
│   ├── prompt.py               the root agent's routing rules
│   ├── config.py               model, API keys, endpoints, output folder
│   ├── sub_agents/             specialists, each with agent.py + prompt.py
│   │   ├── maps_lookup/        facts about named places      (Google Maps grounding)
│   │   ├── maps_lookup_json/   same lookup, returned as JSON (+ schema.py)
│   │   ├── web_search/         "best X in Y", cited answers  (Google Search grounding)
│   │   ├── area_collector/     "all X in an area" batch job  (+ tools.py)
│   │   └── product_survey/     is a product sold here, at what price; menus (+ tools.py)
│   ├── ui/                     local web UI: server.py (FastAPI) + static/ (HTML, CSS, JS)
│   ├── tests/                  pytest suite: offline by default, --live / --billed opt-in
│   ├── tools/                  plain function tools on the root agent
│   │   ├── web_fetch.py        fetch_rendered_page: read a business's own website
│   │   └── output_files.py     read_output_file, convert_json_to_docx
│   ├── pipeline/               the batch jobs behind area_collector and product_survey
│   └── shared/                 helpers used in more than one place
├── run.py                      chat with any agent from the terminal
├── run_collect_places.py       run the collection pipeline directly
├── run_product_survey.py       run a product survey or menu check directly
├── run_ui.py                   start the web UI locally
├── Dockerfile                  container for Cloud Run (see Deploy to Cloud Run)
├── constraints.txt             exact tested package versions, used by the Dockerfile
└── .output/                    collection results: JSON, CSV, DOCX (git-ignored)
```

## How a request flows

```
user ──> place_assistant (root, routes only)
           ├── maps_lookup ─────────> Google Maps grounding
           ├── maps_lookup_json ────> maps lookup step ──> JSON format step
           ├── web_search ──────────> Google Search grounding
           ├── area_collector ──────> pipeline/collect.py
           │                            geocode -> Places search -> per place:
           │                            details, reviews, menu, sentiment, OSM
           │                            -> .output/*.json|csv|docx
           ├── product_survey ──────> pipeline/product_survey.py
           │                            Maps search for the product -> per place:
           │                            reviews, own website + PDF menus,
           │                            delivery-app/blog search -> status + prices
           │                            -> .output/*.json|csv|docx
           ├── fetch_rendered_page ─> the business's own website (Playwright)
           └── read_output_file / convert_json_to_docx ──> .output/
```

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# create .env with the settings in AGENTS.md, section 9
gcloud auth application-default login
```

Every agent needs Vertex AI. `area_collector` and `product_survey` also need a Maps Platform API key with billing enabled.

## Running

Run these from the project root.

```bash
python run_ui.py                          # the Place Assistant web UI: http://localhost:8080
adk web                                   # ADK's developer chat UI with the root agent
adk run place_assistant                   # terminal chat with the root agent

python run.py assistant "Recommend a work-friendly cafe in Jakarta Pusat and show its menu"
python run.py maps      "Compare Fentons Creamery and Tara's Organic Ice Cream in Oakland"
python run.py search    "Best cafes in Jakarta Pusat?"
python run.py json      "Tell me about Blue Bottle Coffee in Oakland"
python run.py collector "Collect bars that serve alcohol in Jakarta Pusat, limit 10"
python run.py survey    "Which restaurants in Menteng, Jakarta sell Aqua mineral water, and at what price? Check 5 places."

python run_collect_places.py "Jakarta Pusat, Indonesia" --type cafe --limit 20 --require-alcohol

# Product survey: is the product sold in an area, and at what price?
python run_product_survey.py "Menteng, Jakarta, Indonesia" --product Aqua \
    --alias "Aqua Reflections" --description "Danone bottled mineral water brand" --type restaurant --limit 10
# The same check for a list of outlets, or full menus when --product is left out
python run_product_survey.py "Jakarta, Indonesia" --product Aqua --places "Sate Khas Senayan Menteng" "Bakmi GM Sarinah"
python run_product_survey.py "Jakarta, Indonesia" --places "Anomali Coffee Menteng"
```

`run.py` can run any specialist on its own, which is the easiest way to test one in isolation.

## Web UI

`python run_ui.py` serves a single page with four tabs:

- **Assistant:** chat with the root agent. Each answer shows which tools it used, and report files it mentions become download links.
- **Product survey:** a form for one product across an area's places, with live progress per place, a Stop button, a summary, a price range, and a table giving each place a plain verdict ("Available", "Likely available", "Needs field check", "Couldn't check fully") with a one-line proof, such as the menu line and price it was found in.
- **Outlets & menus:** a list of named places, checked for a product or read for their full menus with prices.
- **Reports:** every JSON, CSV and Word file in `.output/`, with downloads, and saved surveys reopened in the same views.

Stop ends a survey early: places already being checked finish, the rest are marked "not checked", and the partial report is saved. The server binds to `127.0.0.1` and has no login. Options: `--port`, `--host 0.0.0.0`, `--reload`.

The UI reads its settings only from environment variables, the same ones as the agents, so it carries no project-specific values. Answers from `web_search` show Google's Search Suggestions underneath, as Google's grounding terms require. To put it online, see [Deploy to Cloud Run](#deploy-to-cloud-run).

## Deploy to Cloud Run

The web UI and every agent run as one Cloud Run service, built from the `Dockerfile` (Python 3.11, headless Chromium, the package versions pinned in `constraints.txt`). These steps suit a demo: the service stays private, runs one instance, and checks at most 5 places per run. Run them from the project root, with `gcloud` signed in as an owner of the target project.

On macOS, the terminal's shell (zsh) treats `#` comments as commands when they are pasted. Run `setopt interactivecomments` first in that terminal window. Paste one step at a time and check it finished without an error before the next. The step 0 settings last only for that terminal window.

```bash
# 0. Your settings
PROJECT=your-project-id
REGION=asia-southeast2                  # Jakarta; any Cloud Run region works
SERVICE=place-assistant
SA=place-assistant-run@$PROJECT.iam.gserviceaccount.com

# 1. Turn on the services (once per project, a minute or two)
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com secretmanager.googleapis.com --project $PROJECT

# 2. An identity for the app that may call Vertex AI
gcloud iam service-accounts create place-assistant-run --project $PROJECT --display-name "Place Assistant on Cloud Run"
gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role roles/aiplatform.user

# 3. The Maps key as a secret, read from .env so it never shows on screen
grep '^MAPS_API_KEY=' .env | cut -d= -f2- | tr -d '\n' | \
  gcloud secrets create maps-api-key --project $PROJECT --data-file=-
gcloud secrets add-iam-policy-binding maps-api-key --project $PROJECT \
  --member serviceAccount:$SA --role roles/secretmanager.secretAccessor

# 4. Build and deploy (5-10 minutes the first time; answer Y to creating the image repository)
gcloud run deploy $SERVICE --source . --project $PROJECT --region $REGION \
  --service-account $SA --no-allow-unauthenticated \
  --memory 4Gi --cpu 2 --timeout 3600 --max-instances 1 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,GEMINI_PIPELINE_MODEL=gemini-2.5-flash,MAX_PLACES_PER_RUN=5 \
  --set-secrets MAPS_API_KEY=maps-api-key:latest

# 5. Open it on your laptop for the demo, then browse to http://localhost:8080
gcloud run services proxy $SERVICE --project $PROJECT --region $REGION --port 8080
```

What the flags do, and why:

- `--no-allow-unauthenticated`: nobody can open the URL without Google sign-in and permission. The UI has no login of its own, and every survey spends money. Step 5 signs in as you.
- `--max-instances 1`: chat history and saved reports live inside the running instance, so one instance keeps them in one place. They are lost when the instance stops, which happens about 15 minutes after the last request. Run a survey shortly before the demo, and download the reports you want to keep.
- `--timeout 3600`: surveys stream their progress for minutes; the default 5-minute limit would cut them off.
- `--memory 4Gi --cpu 2`: up to four headless Chromium windows read websites at once.
- `MAX_PLACES_PER_RUN=5`: one survey, outlet check or area collection checks at most 5 places, from the forms and from chat. Raise it for real survey work.
- The Maps key bills the project it was created in, which can differ from `$PROJECT`. To bill `$PROJECT` instead, create a key there with Places API (New) and Geocoding API turned on, restricted to those two APIs, and put it in `.env` before step 3.

**Letting others try it.** Instead of step 5, put Google's sign-in (IAP) in front and allow named accounts:

```bash
gcloud services enable iap.googleapis.com --project $PROJECT
gcloud beta run services update $SERVICE --project $PROJECT --region $REGION --iap
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
gcloud run services add-iam-policy-binding $SERVICE --project $PROJECT --region $REGION \
  --member serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com --role roles/run.invoker
gcloud beta iap web add-iam-policy-binding --project $PROJECT --resource-type cloud-run \
  --service $SERVICE --region $REGION --member user:viewer@example.com --role roles/iap.httpsResourceAccessor
```

Viewers then open the service URL that step 4 printed. Viewers outside your Google Workspace organization may need an OAuth consent screen set up first; Google's "Configure IAP for Cloud Run" guide covers it.

**Afterwards.** Re-run step 4 to deploy a new version. Set a budget alert under Billing → Budgets & alerts. An idle service costs nothing beyond stored images; remove it with `gcloud run services delete $SERVICE --project $PROJECT --region $REGION`.

**If something fails:**

- *Build fails with a permission error:* newer projects build with the Compute Engine default service account, which may need `roles/run.builder` (`gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com --role roles/run.builder`).
- *A red "Vertex AI" pill or 403 errors in chat:* the service account lacks `roles/aiplatform.user`, or Vertex AI isn't enabled in `$PROJECT`.
- *"Maps key missing":* the secret wasn't attached; check `--set-secrets` in step 4.
- *Logs:* `gcloud run services logs read $SERVICE --project $PROJECT --region $REGION --limit 100`.

## Testing

```bash
pip install -r requirements-dev.txt     # adds pytest
pytest                                  # offline suite: free, no network, about a second
pytest --live                           # also checks routing and grounded answers: Vertex AI only
pytest --live --billed                  # also a real product check and a 2-place survey: Maps billing
pytest -k routing --live                # one group only
```

- **Offline tests** cover price parsing, product matching and its anti-invention guards, website reading, robots.txt, key redaction, file sandboxing, the survey and collector pipelines, Word reports, the UI server and the agent wiring rules. Every Google call is faked, and the network is blocked, so a missing fake fails instead of spending money.
- **Live routing tests** send real questions to the root agent and the survey specialist with every tool stubbed. They check which tool the model picks and the arguments it fills in, without running a survey.
- **Live end-to-end tests** run the grounded specialists for real. **Billed tests** run one named-place product check and a 2-place survey.

Live tests check model behaviour, so a failure can mean a prompt needs rewording rather than a code bug. Live data also changes as places update their menus.

## Keeping secrets out of git

- **Secrets live only in `.env`,** which git ignores. The settings it needs are listed in AGENTS.md, section 9.
- **Turn on the secret check once per clone:** `git config core.hooksPath .githooks`. It blocks commits that add `.env` files, Google API keys, private keys or OAuth tokens, and prints only file names.
- **Builds never see `.env`.** `.dockerignore` and `.gcloudignore` keep it, `.venv`, `.output` and local sessions out of container builds. In the cloud, pass the Maps key from a secret manager instead of a file.
- **Restrict the Maps key** to `places.googleapis.com` and `geocoding-backend.googleapis.com`, and set a budget alert on the project.

## Adding a specialist

1. Create `place_assistant/sub_agents/<name>/` with `__init__.py`, `agent.py` and `prompt.py`.
2. Export the agent from `sub_agents/__init__.py`.
3. Add `AgentTool(agent=...)` to the root agent's tools in `agent.py`.
4. Describe when to use it in the root `prompt.py`, using the agent's exact `name`.
5. Optionally add it to `AGENTS` in `run.py`.

Keep every new folder inside `place_assistant/`, or give it a dot-prefixed name. `adk web` treats every other folder in the project root as an agent app and fails on it.

## Constraints worth knowing

- **Grounding tools must be alone.** `google_maps_grounding` and `google_search` can't share an agent with any other tool. That is why each lives in its own specialist, wrapped as an `AgentTool`.
- **The root only sees a specialist's final text.** Specialists put Maps links and source citations in their answer, not just in metadata.
- **The model is Gemini 3.1 Pro** (`gemini-3.1-pro-preview`), a preview model served only from `GOOGLE_CLOUD_LOCATION=global`. Change it with `GEMINI_MODEL` in `.env`. Surveys call Gemini several times per place; set `GEMINI_PIPELINE_MODEL=gemini-2.5-flash`. It is cheaper and faster, and its delivery-app searches cite their sources reliably. On 3.1 Pro most of those searches came back without sources and were discarded.
- **JSON output takes two steps.** On this model, `output_schema` is unreliable when combined with tools, so `maps_lookup_json` looks up first and formats second.
- **Text Search returns at most 60 places per area.** Runs report `capped_at_60` when the cap is hit.
- **No API exposes when a place opened.** Requests like "opened in the last 6 months" can't be answered.
- **No API offers itemized menus with prices.** Menus are read from the business's own site, its menu sub-pages and PDF menus, then from delivery platforms and food blogs, and second-hand prices are flagged as possibly outdated.
- **A product survey finds online evidence, not distribution.** "No evidence" means nothing readable online mentions the product, not that the place doesn't sell it. Confirm key outlets in person.
- **Website fetching is restricted.** Fetchers respect robots.txt, identify themselves, and refuse Google, Bing, social-network and delivery-app pages, also when a link redirects there. Menus kept there are reported as links instead.
- **Search Suggestions must be displayed** in any end-user product built on `web_search`, per Google's grounding terms.

The reasoning behind ruling out the Google Business Profile API is in [GOOGLE_BUSINESS_PROFILE_API_FINDINGS.md](GOOGLE_BUSINESS_PROFILE_API_FINDINGS.md).
