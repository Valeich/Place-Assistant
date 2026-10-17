/* Place Assistant UI. Plain JavaScript, no build step.
 * Talks to place_assistant/ui/server.py: /api/chat, /api/survey, /api/check
 * stream newline-delimited JSON; /api/files lists and serves reports. */
"use strict";

// ---------------------------------------------------------------------------
// Helpers

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Creates an element. props: class, text, on<event>, dataset, other attributes. */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Replaces el's children, skipping null and false entries. */
function setChildren(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
}

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

const fmtNumber = (n) => new Intl.NumberFormat(undefined, { maximumFractionDigits: n >= 100 ? 0 : 2 }).format(n);
const fmtElapsed = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtDate = (value) => new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const fmtSize = (bytes) => bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
const splitList = (text) => (text || "").split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
const humanize = (text) => (text || "").replace(/_/g, " ").replace(/\s+/g, " ").trim();

/** Reads a FastAPI error body into one readable line. */
function errorText(status, body) {
  try {
    const data = JSON.parse(body);
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => `${(d.loc || []).filter((x) => x !== "body").join(".")}: ${d.msg}`).join("; ");
    }
    if (data.detail) return String(data.detail);
  } catch { /* not JSON */ }
  return `Request failed (${status}). ${body.slice(0, 200)}`;
}

/** POSTs body and calls onItem for every NDJSON line of the streamed reply. */
async function streamNdjson(url, body, onItem, signal) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(errorText(res.status, await res.text()));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) onItem(JSON.parse(line));
    }
  }
  if (buffer.trim()) onItem(JSON.parse(buffer));
}

// ---------------------------------------------------------------------------
// Labels shared by every view

const STATUS = {
  found_with_price: { label: "Available, price found", tone: "ok", order: 0 },
  found_no_price: { label: "Available, no price", tone: "info", order: 1 },
  mentioned_in_reviews: { label: "Likely available", tone: "warn", order: 2 },
  menu_found: { label: "Menu found", tone: "ok", order: 0 },
  menu_unavailable: { label: "No menu found", tone: "none", order: 3 },
  no_evidence: { label: "Needs field check", tone: "none", order: 3 },
  inconclusive: { label: "Couldn't check fully", tone: "bad", order: 4 },
  place_not_found: { label: "Place not found", tone: "bad", order: 5 },
  not_checked: { label: "Skipped", tone: "none", order: 6 },
};
const statusInfo = (s) => STATUS[s] || { label: humanize(s), tone: "none", order: 9 };
const badge = (status) => h("span", { class: `badge tone-${statusInfo(status).tone}`, text: statusInfo(status).label });

const SOURCE = {
  official_website: "Restaurant website",
  third_party_search: "Online listing",
  google_reviews: "Google review",
  unavailable: "Not found",
};
const sourceLabel = (s) => SOURCE[s] || humanize(s);

const TOOLS = {
  maps_lookup: "Google Maps",
  maps_lookup_json: "Google Maps (JSON)",
  web_search: "Google Search",
  area_collector: "Area collector",
  product_survey: "Product survey",
  fetch_rendered_page: "Website reader",
  read_output_file: "Report reader",
  convert_json_to_docx: "Word export",
};

const CHECK_NAMES = {
  google_reviews: "Google reviews",
  website: "Website",
  third_party_search: "Delivery apps & online listings",
  place_details: "Place details",
  product_check: "Product check",
  find_place: "Google Maps lookup",
  run: "Run",
};

/** Turns a pipeline check result such as "no_match (2 page(s) read)" into words. */
function describeCheck(value) {
  const v = String(value ?? "");
  const simple = {
    match: "Found",
    no_reviews: "No reviews returned",
    name_found_but_not_the_product: "Name appears, but not as this product",
    no_website: "No website listed",
    social_media_only: "Website is a social profile, which can't be read",
    not_allowed: "Website host is never fetched",
    blocked_by_robots: "Site asks bots not to read it (robots.txt)",
    broken_link: "Website link is broken (404)",
    fetch_failed: "Website couldn't be loaded",
    no_sources: "Search cited no sources",
    nothing_found: "Nothing found",
    no_menu_text: "No menu on the pages read",
  };
  if (simple[v]) return document.createTextNode(simple[v]);
  if (v.startsWith("match (menu not read:")) {
    const links = v.slice("match (menu not read:".length, -1).trim().split(/\s+/).filter(Boolean);
    return h("span", {}, "Found, but a menu page couldn't be read and may hold the price: ",
      ...links.map((url, i) => [i ? ", " : "", h("a", { href: url, target: "_blank", rel: "noopener", text: "open menu" })]));
  }
  if (v.startsWith("no_match")) return document.createTextNode("Not mentioned" + v.slice("no_match".length).replace("page(s)", "pages"));
  if (v.startsWith("menu_not_readable:")) {
    const links = v.slice("menu_not_readable:".length).trim().split(/\s+/).filter(Boolean);
    return h("span", {}, "Menu is linked but can't be read automatically: ",
      ...links.map((url, i) => [i ? ", " : "", h("a", { href: url, target: "_blank", rel: "noopener", text: "open menu" })]));
  }
  if (v.startsWith("error:")) return h("span", { class: "muted", text: "Error: " + v.slice(6).trim() });
  if (v.startsWith("skipped")) return document.createTextNode(v.charAt(0).toUpperCase() + v.slice(1));
  return document.createTextNode(humanize(v));
}

// ---------------------------------------------------------------------------
// App state and settings

const state = { health: null, knownFiles: new Set() };

async function loadHealth() {
  const env = $("#env");
  try {
    const res = await fetch("/api/health");
    state.health = await res.json();
  } catch {
    env.replaceChildren(h("span", { class: "pill bad" }, h("span", { class: "dot" }), "Server not reachable"));
    return;
  }
  const hl = state.health;
  env.replaceChildren(
    h("span", { class: "pill", title: hl.pipeline_model && hl.pipeline_model !== hl.model
      ? `Agents use ${hl.model}; survey steps use ${hl.pipeline_model}` : "Model used by every agent and survey step" },
      hl.pipeline_model && hl.pipeline_model !== hl.model ? `${hl.model} + ${hl.pipeline_model}` : hl.model),
    h("span", { class: `pill ${hl.vertex_ai && hl.project ? "ok" : "bad"}`, title: "Vertex AI project and location" },
      h("span", { class: "dot" }), hl.project ? `${hl.project} · ${hl.location || "no location"}` : "Vertex AI not configured"),
    h("span", { class: `pill ${hl.maps_key ? "ok" : "bad"}`, title: "Maps Platform key, needed for surveys and menu checks" },
      h("span", { class: "dot" }), hl.maps_key ? "Maps key set" : "Maps key missing"),
  );
  for (const notice of $$(".maps-missing")) notice.hidden = hl.maps_key;
  // A deployment can cap places per run (MAX_PLACES_PER_RUN); keep the forms within it.
  const cap = hl.max_places_per_run || 60;
  for (const input of $$("#survey-form [name=limit], #survey-form [name=limit_range]")) {
    input.max = cap;
    if (Number(input.value) > cap) input.value = cap;
  }
  for (const form of [$("#survey-form"), $("#check-form")]) $("[data-run]", form).disabled = !hl.maps_key;
  $("#reports-dir").textContent = `Saved in ${hl.output_dir}`;
}

async function refreshKnownFiles() {
  try {
    const files = await (await fetch("/api/files")).json();
    state.knownFiles = new Set(files.map((f) => f.name));
    return files;
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Tabs

const TABS = ["chat", "survey", "check", "reports"];

function showTab(name) {
  if (!TABS.includes(name)) name = "chat";
  for (const tab of $$(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  for (const panel of $$(".panel")) panel.hidden = panel.id !== `panel-${name}`;
  window.scrollTo(0, 0);
  if (name === "reports") loadReports();
  if (name === "chat") $("#chat-input").focus();
}

function initTabs() {
  for (const tab of $$(".tab")) {
    tab.addEventListener("click", () => {
      // Clicking the tab you're on resets it, e.g. back to the report list.
      if (location.hash.slice(1) === tab.dataset.tab) showTab(tab.dataset.tab);
      else location.hash = tab.dataset.tab;
    });
  }
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));
}

// ---------------------------------------------------------------------------
// Report links inside chat answers

function reportLink(name) {
  return h("a", { class: "report-link", href: `/api/files/${encodeURIComponent(name)}`, title: "Download report", text: name });
}

/** Replaces report paths and known report names in rendered text with download links. */
function linkifyReports(root) {
  const re = /(?:[^\s`'"<>()]*[\\/]\.output[\\/])?([\w.\-]+\.(?:json|csv|docx))/g;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (node.parentElement.closest("a")) continue;
    const text = node.nodeValue;
    re.lastIndex = 0;
    let last = 0;
    let match;
    const parts = [];
    while ((match = re.exec(text))) {
      const name = match[1];
      if (match[0] === name && !state.knownFiles.has(name)) continue;
      parts.push(text.slice(last, match.index), reportLink(name));
      last = match.index + match[0].length;
    }
    if (!parts.length) continue;
    parts.push(text.slice(last));
    const code = node.parentElement.tagName === "CODE" && node.parentElement.childNodes.length === 1 ? node.parentElement : null;
    (code || node).replaceWith(...parts);
  }
}

function renderMarkdown(text) {
  const el = h("div", { class: "md" });
  if (window.marked && window.DOMPurify) {
    el.innerHTML = DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }));
    for (const a of $$("a", el)) { a.target = "_blank"; a.rel = "noopener"; }
  } else {
    el.style.whiteSpace = "pre-wrap";
    el.textContent = text;
  }
  linkifyReports(el);
  return el;
}

// ---------------------------------------------------------------------------
// Assistant chat

const chat = { sessionId: null, controller: null };

const EXAMPLES = [
  { tag: "Product survey", text: "Which restaurants in Menteng, Jakarta sell Aqua mineral water, and at what price? Check 5 places." },
  { tag: "Outlet check", text: "Does Sate Khas Senayan in Menteng, Jakarta sell Aqua?" },
  { tag: "Menu", text: "Show me the menu with prices at Anomali Coffee Menteng, Jakarta" },
  { tag: "Place facts", text: "What's the address, rating and opening hours of Bakmi GM Sarinah, Jakarta?" },
];

function scrollChat() {
  const log = $("#chat-log");
  log.scrollTop = log.scrollHeight;
}

function assistantMessage() {
  const activity = h("div", { class: "activity" });
  const thinkingLabel = h("span", { text: "Thinking" });
  const thinking = h("div", { class: "thinking" }, h("span", { class: "spinner" }), thinkingLabel);
  const body = h("div", { class: "bubble", hidden: true });
  const suggestions = h("div", { class: "search-suggestions" });
  const root = h("div", { class: "msg assistant" }, activity, body, suggestions, thinking);
  $("#chat-log").append(root);
  const chips = []; // [{ name, chip }] in call order
  const started = Date.now();
  const timer = setInterval(() => {
    const busyTools = chips.filter((c) => !c.chip.classList.contains("done")).length;
    thinkingLabel.textContent = `${busyTools ? "Working" : "Thinking"} · ${fmtElapsed(Date.now() - started)}`;
  }, 1000);

  return {
    toolStarted(name, args) {
      const chip = h("span", { class: "tool-chip", title: args && args.request ? args.request : "" },
        h("span", { class: "spinner" }), TOOLS[name] || humanize(name));
      chips.push({ name, chip });
      activity.append(chip);
      scrollChat();
    },
    toolDone(name) {
      const open = chips.find((c) => c.name === name && !c.chip.classList.contains("done"));
      if (open) {
        open.chip.classList.add("done");
        open.chip.querySelector(".spinner")?.remove();
      }
    },
    addText(text) {
      body.hidden = false;
      body.append(renderMarkdown(text));
      scrollChat();
    },
    addSearchSuggestions(html) {
      // Google's own HTML, shown unchanged as its grounding terms require.
      // The sandbox (no scripts, no same-origin) keeps it apart from the page,
      // and <base target> opens its search links in a new tab.
      suggestions.append(h("iframe", {
        title: "Google Search suggestions",
        sandbox: "allow-popups allow-popups-to-escape-sandbox",
        referrerpolicy: "no-referrer",
        srcdoc: `<base target="_blank">${html}`,
      }));
      scrollChat();
    },
    addError(message) {
      root.insertBefore(h("div", { class: "notice bad", text: message }), thinking);
      scrollChat();
    },
    addNote(message) {
      root.insertBefore(h("div", { class: "notice", text: message }), thinking);
    },
    finish() {
      clearInterval(timer);
      thinking.remove();
      for (const { chip } of chips) chip.querySelector(".spinner")?.remove();
      linkifyReports(body);
    },
  };
}

// A menu item that bundles the product with something else, such as
// "Steak 200gr + 1 Le Minerale". Matches prices.is_bundle on the server.
function isBundle(item) {
  return /\+|\b(paket|combo|bundle|package|set menu)\b/i.test(item || "");
}

function setChatBusy(busy) {
  $("#chat-send").disabled = busy;
  $("#chat-stop").hidden = !busy;
  $("#chat-input").disabled = busy;
}

async function sendChat(text) {
  text = text.trim();
  if (!text || chat.controller) return;
  $("#chat-welcome")?.remove();
  $("#chat-log").append(h("div", { class: "msg user" }, h("div", { class: "bubble", text })));
  const reply = assistantMessage();
  scrollChat();
  chat.controller = new AbortController();
  setChatBusy(true);
  try {
    await streamNdjson("/api/chat", { message: text, session_id: chat.sessionId }, (item) => {
      if (item.type === "session") chat.sessionId = item.session_id;
      else if (item.type === "tool_call") reply.toolStarted(item.name, item.args);
      else if (item.type === "tool_result") reply.toolDone(item.name);
      else if (item.type === "message") reply.addText(item.text);
      else if (item.type === "search_suggestions") reply.addSearchSuggestions(item.html);
      else if (item.type === "error") reply.addError(item.message);
    }, chat.controller.signal);
  } catch (err) {
    if (err.name === "AbortError") reply.addNote("Stopped. A survey already running may still finish and save its report.");
    else reply.addError(err.message);
  } finally {
    chat.controller = null;
    setChatBusy(false);
    await refreshKnownFiles();
    reply.finish();
    $("#chat-input").focus();
  }
}

function resetChat() {
  chat.controller?.abort();
  chat.sessionId = null;
  const log = $("#chat-log");
  log.replaceChildren(welcomeBlock());
  $("#chat-input").focus();
}

function welcomeBlock() {
  return h("div", { class: "welcome", id: "chat-welcome" },
    h("h1", { text: "Ask about places, products and menus" }),
    h("p", { text: "The assistant picks the right tool for each question: Google Maps, Google Search, the product survey or the area collector. Answers come from those sources, never from memory." }),
    h("div", { class: "examples" }, EXAMPLES.map((ex) =>
      h("button", { class: "example", type: "button", onclick: () => sendChat(ex.text) }, h("small", { text: ex.tag }), ex.text))),
  );
}

function initChat() {
  $("#chat-log").replaceChildren(welcomeBlock());
  const input = $("#chat-input");
  const autosize = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; };
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value;
    input.value = "";
    autosize();
    sendChat(text);
  });
  $("#chat-stop").addEventListener("click", () => chat.controller?.abort());
  $("#chat-new").addEventListener("click", resetChat);
}

// ---------------------------------------------------------------------------
// Running a survey or check, with live progress

function progressView() {
  const stageLabel = h("span", { text: "Starting" });
  const time = h("span", { class: "progress-time", text: "0:00" });
  const fill = h("div");
  const bar = h("div", { class: "bar indeterminate" }, fill);
  const ticker = h("div", { class: "ticker", "aria-live": "polite" });
  const note = h("div", { class: "notice", hidden: true });
  const el = h("div", { class: "card progress-card" },
    h("div", { class: "progress-top" }, h("div", { class: "progress-stage" }, h("span", { class: "spinner" }), stageLabel), time),
    bar, note, ticker);
  const rows = [];
  const started = Date.now();
  const timer = setInterval(() => { time.textContent = fmtElapsed(Date.now() - started); }, 1000);

  return {
    el,
    update(ev) {
      if (ev.stage === "geocoding") stageLabel.textContent = `Finding ${ev.area} on Google Maps`;
      else if (ev.stage === "searching") {
        stageLabel.textContent = "Searching Google Maps for places";
        if (ev.widened) { note.hidden = false; note.textContent = "Google Maps returned a very small area, so the search was widened to about 2 km."; }
      } else if (ev.stage === "checking") {
        stageLabel.textContent = ev.total ? `Checking ${ev.total} place${ev.total === 1 ? "" : "s"}` : "No places found";
        bar.classList.remove("indeterminate");
        ticker.replaceChildren(...(ev.names || []).map((name) => {
          const status = h("span", { class: "pending", text: "Waiting" });
          rows.push(status);
          return h("div", { class: "ticker-row" }, h("span", { class: "name", text: name || "Unnamed place" }), status);
        }));
      } else if (ev.stage === "place_done") {
        stageLabel.textContent = `Checked ${ev.done} of ${ev.total}`;
        fill.style.width = `${(ev.done / ev.total) * 100}%`;
        if (rows[ev.index]) rows[ev.index].replaceWith(badge(ev.status));
      } else if (ev.stage === "saving") {
        stageLabel.textContent = "Saving reports";
      }
    },
    stopped() {
      clearInterval(timer);
      el.querySelector(".spinner")?.remove();
      stageLabel.textContent = "Stopped";
      note.hidden = false;
      note.className = "notice warn";
      note.textContent = "Places already being checked will finish, and the partial result is saved. Open it from Reports in a minute.";
    },
    dispose() { clearInterval(timer); },
  };
}

const errorBox = (message) => h("div", { class: "notice bad", role: "alert" }, h("strong", { text: "Couldn't finish. " }), message);

async function runJob({ url, body, form, results }) {
  const run = $("[data-run]", form);
  const stop = $("[data-stop]", form);
  const controller = new AbortController();
  const progress = progressView();
  results.replaceChildren(progress.el);
  run.disabled = true;
  stop.hidden = false;
  stop.onclick = () => controller.abort();
  let finished = false;
  try {
    await streamNdjson(url, body, (item) => {
      if (item.type === "progress") progress.update(item);
      else if (item.type === "result") { finished = true; renderResult(results, item.data); }
      else if (item.type === "error") { finished = true; results.replaceChildren(errorBox(item.message)); }
    }, controller.signal);
    if (!finished) results.replaceChildren(errorBox("The connection closed before a result arrived. Check Reports for a saved file."));
  } catch (err) {
    if (err.name === "AbortError") progress.stopped();
    else results.replaceChildren(errorBox(err.message));
  } finally {
    progress.dispose();
    run.disabled = !(state.health && state.health.maps_key);
    stop.hidden = true;
    refreshKnownFiles();
  }
}

// ---------------------------------------------------------------------------
// Result views

function downloads(files) {
  return h("div", { class: "downloads" },
    ["docx", "csv", "json"].filter((k) => files && files[k]).map((k) =>
      h("a", { class: "btn small", href: `/api/files/${encodeURIComponent(files[k])}`, title: files[k], text: { docx: "Word", csv: "CSV", json: "JSON" }[k] })));
}

function renderResult(container, data) {
  const kind = data.summary && data.summary.kind;
  if (kind === "product_survey") renderProduct(container, data);
  else if (kind === "menu_check") renderMenus(container, data);
  else container.replaceChildren(errorBox("This file isn't a survey or menu check."));
}

function placeCell(place) {
  const searched = place.searched_name && place.name && place.searched_name !== place.name;
  return h("td", { class: "place" },
    h("div", { class: "place-name", text: place.name || place.searched_name || "Unknown place" }),
    place.address ? h("div", { class: "place-addr", text: place.address }) : null,
    searched ? h("div", { class: "place-note", text: `Searched for "${place.searched_name}"` }) : null);
}

function placeLinks(place) {
  return h("div", { class: "links" },
    place.google_maps_url ? h("a", { href: place.google_maps_url, target: "_blank", rel: "noopener", text: "Maps", onclick: (e) => e.stopPropagation() }) : null,
    place.website ? h("a", { href: place.website, target: "_blank", rel: "noopener", text: "Website", onclick: (e) => e.stopPropagation() }) : null,
    place.phone ? h("a", { href: `tel:${place.phone.replace(/\s/g, "")}`, text: place.phone, onclick: (e) => e.stopPropagation() }) : null);
}

function priceLine(entry) {
  return h("div", { class: "price-line" },
    h("span", { class: "item", text: entry.item }),
    entry.price ? h("span", { class: "amt", text: entry.price }) : h("span", { class: "read", text: "no price shown" }),
    entry.price_read_as && entry.price_read_as !== entry.price ? h("span", { class: "read", text: `= ${entry.price_read_as}` }) : null);
}

function evidenceDetail(place) {
  const blocks = [];
  const evidence = place.evidence || [];
  if (evidence.length) {
    blocks.push(h("div", { class: "detail-block" }, h("h4", { text: "Evidence" }),
      h("div", { class: "evidence" }, evidence.map((e) =>
        h("div", { class: "ev" },
          h("div", { class: "ev-top" },
            h("span", { class: `badge tone-${e.source === "google_reviews" ? "warn" : e.source === "official_website" ? "ok" : "info"}`, text: sourceLabel(e.source) }),
            priceLine(e),
            e.url ? h("a", { href: e.url, target: "_blank", rel: "noopener", text: "open page" }) : null),
          e.quote ? h("div", { class: "ev-quote", text: `“${e.quote}”` }) : null,
          e.source === "third_party_search" ? h("div", { class: "read", text: "From an online listing found through Google Search (a delivery app, food blog or social post), not the place's own site. The price may be outdated." }) : null)))));
  }
  const checks = place.checks || {};
  if (Object.keys(checks).length) {
    blocks.push(h("div", { class: "detail-block" }, h("h4", { text: "What was checked" }),
      h("dl", { class: "checks" }, Object.entries(checks).map(([k, v]) => [h("dt", { text: CHECK_NAMES[k] || humanize(k) }), h("dd", {}, describeCheck(v))]))));
  }
  const sources = place.third_party_sources || [];
  if (sources.length || place.third_party_summary) {
    blocks.push(h("div", { class: "detail-block" }, h("h4", { text: "Google Search" }),
      sources.length ? h("div", { class: "links" }, sources.map((s) => h("a", { href: s.uri, target: "_blank", rel: "noopener", text: s.title || "source" }))) : null,
      place.third_party_summary ? h("p", { class: "search-summary", text: place.third_party_summary }) : null));
  }
  return h("div", { class: "detail-grid" }, blocks.length ? blocks : h("p", { class: "muted", text: "No further detail." }));
}

function statCards(summary, onFilter) {
  const counts = summary.status_counts || {};
  const cards = [];
  const all = h("button", { class: "stat", type: "button", "aria-pressed": "true" },
    h("span", { class: "num", text: summary.checked ?? 0 }), h("span", { class: "lbl", text: "Places checked" }));
  cards.push([null, all]);
  for (const [status, count] of Object.entries(counts).sort((a, b) => statusInfo(a[0]).order - statusInfo(b[0]).order)) {
    if (!count) continue;
    const info = statusInfo(status);
    cards.push([status, h("button", { class: "stat", type: "button", "aria-pressed": "false", title: `Show only: ${info.label}` },
      h("span", { class: "num", text: count }),
      h("span", { class: "lbl" }, h("i", { class: `tone-${info.tone}`, style: "background: currentColor" }), info.label))]);
  }
  for (const [status, card] of cards) {
    card.addEventListener("click", () => {
      for (const [, c] of cards) c.setAttribute("aria-pressed", String(c === card));
      onFilter(status);
    });
  }
  return h("div", { class: "stats" }, cards.map(([, c]) => c));
}

function renderProduct(container, data) {
  const s = data.summary;
  const places = [...data.places].map((p, i) => ({ ...p, _i: i }))
    .sort((a, b) => statusInfo(a.status).order - statusInfo(b.status).order || a._i - b._i);
  const named = Array.isArray(s.place_names);
  const title = named ? `${s.product} at ${s.place_names.length} named place${s.place_names.length === 1 ? "" : "s"}` : `${s.product} in ${s.area}`;
  const meta = [
    named ? s.area : (s.place_types || []).map(humanize).join(", "),
    s.aliases && s.aliases.length ? `also searched as ${s.aliases.join(", ")}` : null,
    s.generated_at ? fmtDate(s.generated_at) : null,
  ].filter(Boolean).join(" · ");

  const notes = h("div", { class: "notes" },
    h("div", { class: "notice info", text: "Each place shows its proof. Available means the product is listed on the place's own menu or an online listing such as a delivery app. Needs field check means the product isn't mentioned online for that place yet; a call or visit will confirm whether it's stocked." }),
    s.limit_note ? h("div", { class: "notice warn", text: s.limit_note }) : null,
    s.area_note ? h("div", { class: "notice warn", text: s.area_note }) : null,
    s.stopped_early ? h("div", { class: "notice warn", text: "This run was stopped early. Places marked Not checked weren't checked." }) : null,
    !named && s.maps_product_hits != null ? h("p", { class: "muted", text: `${s.maps_product_hits} place${s.maps_product_hits === 1 ? "" : "s"} came from Google Maps' search for ${s.product} and were checked first. A Maps match alone isn't evidence.${s.skipped_closed ? ` ${s.skipped_closed} permanently closed place${s.skipped_closed === 1 ? " was" : "s were"} skipped.` : ""}` }) : null);

  const p = s.price_summary;
  const priceCard = p ? h("div", { class: "card price-card" },
    h("div", {}, h("div", { class: "muted", text: p.min === p.max ? "Price" : "Price range" }),
      h("div", { class: "big", text: `${p.currency || ""} ${p.min === p.max ? fmtNumber(p.min) : `${fmtNumber(p.min)} – ${fmtNumber(p.max)}`}`.trim() })),
    h("div", {}, h("div", { class: "muted", text: "Median" }), h("div", { class: "big", text: fmtNumber(p.median) })),
    h("div", {}, h("div", { class: "muted", text: "Prices found" }), h("div", { class: "big", text: p.count })),
    h("p", { class: "muted", style: "flex-basis: 100%", text: "Sizes and variants differ between places, so compare items, not just numbers." })) : null;

  const tbody = h("tbody");
  const countLabel = h("span", { class: "count" });
  const table = h("div", { class: "card table-card" },
    h("div", { class: "filters" }, h("strong", { text: "Places" }), countLabel),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["", "Place", "Result", "Price", "Proof", "Links"].map((t) => h("th", { text: t })))),
      tbody)));

  function fill(filter) {
    const shown = places.filter((pl) => !filter || pl.status === filter);
    countLabel.textContent = `${shown.length} of ${places.length}`;
    tbody.replaceChildren(...shown.flatMap((place) => {
      const ev = place.evidence || [];
      // Same order as the proof: the product alone before a bundle, then the place's own menu first.
      const rank = (e) => (isBundle(e.item) ? 2 : 0) + (e.source === "official_website" ? 0 : 1);
      const best = ev.filter((e) => e.source !== "google_reviews" && e.price).sort((a, b) => rank(a) - rank(b))[0];
      const detail = h("tr", { class: "detail", hidden: true }, h("td", { colspan: 6 }, evidenceDetail(place)));
      const row = h("tr", { class: "row", tabindex: 0, "aria-expanded": "false" },
        h("td", {}, h("span", { class: "chev", text: "›" })),
        placeCell(place),
        h("td", {}, badge(place.status)),
        h("td", { class: "price-cell" }, best ? h("div", { class: "prices" },
          h("span", { class: "amt", text: best.price }),
          best.price_read_as && best.price_read_as !== best.price ? h("span", { class: "read", text: `= ${best.price_read_as}` }) : null)
          : h("span", { class: "muted", text: "-" })),
        h("td", { class: "proof" }, place.proof || "-"),
        h("td", {}, placeLinks(place)));
      const toggle = () => {
        const open = row.getAttribute("aria-expanded") !== "true";
        row.setAttribute("aria-expanded", String(open));
        detail.hidden = !open;
      };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
      return [row, detail];
    }));
  }
  fill(null);

  setChildren(container,
    h("div", { class: "result-head" }, h("div", {}, h("h2", { text: title }), h("div", { class: "meta", text: meta })), downloads(data.files)),
    notes, statCards(s, fill), priceCard, table);
}

function renderMenus(container, data) {
  const s = data.summary;
  const cards = data.places.map((place) => {
    const menu = place.menu || {};
    const items = menu.items || [];
    const third = menu.source === "third_party_search";
    return h("div", { class: "card menu-card" },
      h("div", { class: "menu-card-head" },
        h("div", {},
          h("div", { class: "place-name", text: place.name || place.searched_name }),
          place.address ? h("div", { class: "place-addr", text: place.address }) : null,
          place.searched_name && place.name && place.searched_name !== place.name ? h("div", { class: "place-note", text: `Searched for "${place.searched_name}"` }) : null,
          h("div", { style: "margin-top: 8px" }, placeLinks(place))),
        h("div", { class: "links" }, badge(place.status),
          menu.source && menu.source !== "unavailable" ? h("span", { class: `badge tone-${third ? "info" : "ok"}`, text: sourceLabel(menu.source) }) : null)),
      items.length ? h("div", { class: "table-wrap" }, h("table", { class: "menu-table" },
        h("thead", {}, h("tr", {}, h("th", { text: "Item" }), h("th", { text: "Price" }), h("th", { text: "Read as" }))),
        h("tbody", {}, items.map((it) => h("tr", {},
          h("td", { text: it.item }), h("td", { class: "num", text: it.price || "-" }),
          h("td", { class: "num muted", text: it.price_read_as && it.price_read_as !== it.price ? it.price_read_as : "" })))))) : null,
      third ? h("div", { class: "notice info", text: menu.note || "From an online listing, not the place's own site. Prices may be outdated." }) : null,
      !items.length ? h("div", { class: "notice", text: menu.note || "No menu or prices were found for this place." }) : null,
      menu.website_status && menu.website_status.startsWith("menu_not_readable") ? h("p", {}, describeCheck(menu.website_status)) : null,
      (menu.sources || []).length ? h("div", { class: "links" }, h("span", { class: "muted", text: "Sources:" }),
        menu.sources.map((src) => h("a", { href: src.uri, target: "_blank", rel: "noopener", text: src.title || "source" }))) : null);
  });
  const named = s.place_names || [];
  setChildren(container,
    h("div", { class: "result-head" },
      h("div", {}, h("h2", { text: `Menus for ${named.length} place${named.length === 1 ? "" : "s"}` }),
        h("div", { class: "meta", text: [s.area, s.generated_at ? fmtDate(s.generated_at) : null].filter(Boolean).join(" · ") })),
      downloads(data.files)),
    s.stopped_early ? h("div", { class: "notice warn", text: "This run was stopped early. Places marked Not checked weren't checked." }) : null,
    statCards(s, (status) => {
      cards.forEach((card, i) => { card.hidden = Boolean(status) && data.places[i].status !== status; });
    }),
    h("div", { class: "menu-cards" }, cards));
}

/** Older area_collector runs: a JSON list of place records. */
function renderCollection(container, records, files) {
  const rows = records.map((r) => {
    const menu = r.menu || {};
    return h("tr", {},
      h("td", {}, h("div", { class: "place-name", text: r.name }), h("div", { class: "place-addr", text: r.address || "" })),
      h("td", { text: r.rating != null ? `${r.rating} (${r.total_reviews ?? 0})` : "-" }),
      h("td", { text: humanize((r.price_level || "-").replace("PRICE_LEVEL_", "")).toLowerCase() }),
      h("td", { text: r.alcohol && r.alcohol.serves_alcohol ? "Yes" : "-" }),
      h("td", {}, (menu.items || []).length ? h("div", { class: "prices" }, menu.items.slice(0, 4).map(priceLine),
        menu.items.length > 4 ? h("span", { class: "read", text: `+${menu.items.length - 4} more` }) : null) : h("span", { class: "muted", text: "-" })),
      h("td", { text: sourceLabel(menu.source || "unavailable") }),
      h("td", {}, placeLinks(r)));
  });
  setChildren(container,
    h("div", { class: "result-head" }, h("div", {}, h("h2", { text: `Collection: ${records.length} places` })), downloads(files)),
    h("div", { class: "card table-card" }, h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Place", "Rating", "Price", "Alcohol", "Menu", "Menu source", "Links"].map((t) => h("th", { text: t })))),
      h("tbody", {}, rows)))));
}

// ---------------------------------------------------------------------------
// Forms

const PLACE_TYPES = ["restaurant", "cafe", "coffee_shop", "fast_food_restaurant", "food_court", "bakery", "bar"];

function saveForm(form, key) {
  const values = {};
  for (const el of form.elements) {
    if (!el.name || el.name === "limit_range") continue;
    if (el.type === "checkbox" && el.name === "place_type") (values.place_type ||= []).push(...(el.checked ? [el.value] : []));
    else if (el.type === "checkbox") values[el.name] = el.checked;
    else if (el.type === "radio") { if (el.checked) values[el.name] = el.value; }
    else values[el.name] = el.value;
  }
  store.set(key, values);
}

function restoreForm(form, key) {
  const values = store.get(key, null);
  if (!values) return;
  for (const el of form.elements) {
    if (!el.name || !(el.name in values)) continue;
    if (el.type === "checkbox" && el.name === "place_type") el.checked = values.place_type.includes(el.value);
    else if (el.type === "checkbox") el.checked = Boolean(values[el.name]);
    else if (el.type === "radio") el.checked = values[el.name] === el.value;
    else el.value = values[el.name];
  }
}

function markInvalid(el, invalid) {
  el.classList.toggle("invalid", invalid);
  if (invalid) el.focus();
  return invalid;
}

function initSurveyForm() {
  const form = $("#survey-form");
  $("#survey-types").replaceChildren(...PLACE_TYPES.map((type) =>
    h("label", { class: "chip" },
      h("input", { type: "checkbox", name: "place_type", value: type, checked: type === "restaurant" }),
      h("span", { text: humanize(type) }))));
  restoreForm(form, "pa.survey");

  const range = form.elements.limit_range;
  const number = form.elements.limit;
  range.value = number.value;
  range.addEventListener("input", () => { number.value = range.value; });
  number.addEventListener("input", () => { range.value = number.value; });

  const selectedTypes = () => {
    const types = $$("input[name=place_type]:checked", form).map((el) => el.value);
    const custom = form.elements.custom_type.value.trim().toLowerCase().replace(/\s+/g, "_");
    if (custom && !types.includes(custom)) types.push(custom);
    return types;
  };
  form.addEventListener("change", (e) => {
    if (e.target.name === "place_type" && e.target.checked && selectedTypes().length > 3) e.target.checked = false;
  });
  form.addEventListener("input", () => saveForm(form, "pa.survey"));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const product = form.elements.product;
    const area = form.elements.area;
    if (markInvalid(product, !product.value.trim()) || markInvalid(area, area.value.trim().length < 2)) return;
    const types = selectedTypes();
    if (!types.length || types.length > 3) {
      $("#survey-results").replaceChildren(errorBox("Pick one to three place types."));
      return;
    }
    const limit = Math.max(1, Math.min(state.health?.max_places_per_run || 60, parseInt(number.value, 10) || 10));
    if (limit > 20 && !confirm(`This checks up to ${limit} places, and each one is billed. Continue?`)) return;
    runJob({
      url: "/api/survey",
      form,
      results: $("#survey-results"),
      body: {
        product: product.value.trim(),
        aliases: splitList(form.elements.aliases.value),
        description: form.elements.description.value.trim(),
        area: area.value.trim(),
        place_types: types,
        limit,
        search_third_party: form.elements.search_third_party.checked,
      },
    });
  });
}

function initCheckForm() {
  const form = $("#check-form");
  restoreForm(form, "pa.check");
  const productFields = $(".product-fields", form);
  const run = $("[data-run]", form);
  const syncMode = () => {
    const menu = form.elements.mode.value === "menu";
    productFields.hidden = menu;
    run.textContent = menu ? "Get menus" : "Check places";
  };
  syncMode();
  form.addEventListener("change", (e) => { if (e.target.name === "mode") syncMode(); });
  form.addEventListener("input", () => saveForm(form, "pa.check"));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const menu = form.elements.mode.value === "menu";
    const places = form.elements.places.value.split("\n").map((s) => s.trim()).filter(Boolean);
    const area = form.elements.area;
    const product = form.elements.product;
    const maxPlaces = state.health?.max_places || 30;
    if (markInvalid(form.elements.places, !places.length || places.length > maxPlaces)) {
      $("#check-results").replaceChildren(errorBox(`List one to ${maxPlaces} places, one per line.`));
      return;
    }
    if (markInvalid(area, area.value.trim().length < 2)) return;
    if (!menu && markInvalid(product, !product.value.trim())) return;
    runJob({
      url: "/api/check",
      form,
      results: $("#check-results"),
      body: {
        place_names: places,
        area: area.value.trim(),
        product: menu ? "" : product.value.trim(),
        aliases: menu ? [] : splitList(form.elements.aliases.value),
        description: menu ? "" : form.elements.description.value.trim(),
        search_third_party: form.elements.search_third_party.checked,
      },
    });
  });
}

// ---------------------------------------------------------------------------
// Reports

const KIND_LABELS = { product_survey: "Product survey", menu_check: "Menu check", collection: "Collection", file: "File" };

async function loadReports() {
  const list = $("#reports-list");
  $("#report-view").hidden = true;
  list.hidden = false;
  list.replaceChildren(h("p", { class: "muted", text: "Loading reports" }));
  const files = await refreshKnownFiles();
  const groups = new Map();
  for (const f of files) {
    const g = groups.get(f.stem) || { stem: f.stem, files: {}, modified: 0, kind: "file", title: null };
    g.files[f.format] = f.name;
    g.modified = Math.max(g.modified, f.modified);
    if (f.format === "json") { g.kind = f.kind; g.title = f.title; }
    g.size = (g.size || 0) + f.size;
    groups.set(f.stem, g);
  }
  if (!groups.size) {
    list.replaceChildren(h("div", { class: "empty" }, h("p", { text: "No reports yet. Run a survey, a menu check or an area collection and its files appear here." })));
    return;
  }
  list.replaceChildren(...[...groups.values()].sort((a, b) => b.modified - a.modified).map((g) =>
    h("div", { class: "card report-row" },
      h("div", {},
        h("div", { class: "report-title", text: g.title || humanize(g.stem) }),
        g.title ? h("div", { class: "report-meta", text: g.stem }) : null,
        h("div", { class: "report-meta" },
          h("span", { class: `badge tone-${g.kind === "product_survey" ? "ok" : g.kind === "menu_check" ? "info" : "none"}`, text: KIND_LABELS[g.kind] || "File" }),
          h("span", { text: fmtDate(g.modified) }), h("span", { text: fmtSize(g.size) }))),
      h("div", { class: "report-actions" },
        g.files.json && g.kind !== "file" ? h("button", { class: "btn small primary", type: "button", text: "Open", onclick: () => openReport(g) }) : null,
        downloads(g.files)))));
}

async function openReport(group) {
  const view = $("#report-view");
  const body = h("div", { class: "results" }, h("p", { class: "muted", text: "Loading" }));
  view.replaceChildren(h("button", { class: "btn ghost back", type: "button", text: "‹ All reports", onclick: loadReports }), body);
  $("#reports-list").hidden = true;
  view.hidden = false;
  try {
    const res = await fetch(`/api/reports/${encodeURIComponent(group.files.json)}`);
    if (!res.ok) throw new Error(errorText(res.status, await res.text()));
    const data = await res.json();
    if (data.collection) renderCollection(body, data.collection, group.files);
    else renderResult(body, { ...data, files: group.files });
  } catch (err) {
    body.replaceChildren(errorBox(err.message));
  }
}

// ---------------------------------------------------------------------------

function init() {
  initTabs();
  initChat();
  initSurveyForm();
  initCheckForm();
  $("#reports-refresh").addEventListener("click", loadReports);
  loadHealth();
  refreshKnownFiles();
}

init();
