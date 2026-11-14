// Logic of the panel (index.html) that can run without a DOM: pulled out of the page source and evaluated.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "..", "astro_id", "facet", "static", "index.html"), "utf8");

function between(a, b) {
  const i = html.indexOf(a), j = html.indexOf(b, i);
  assert.ok(i >= 0 && j > i, `markers not found: ${a} .. ${b}`);
  return html.slice(i, j);
}

function load(mode, cut) {
  const src = between("const MAX_INDEXED", "const nSave") + "const nSave = (r) => searchMode() ? indexedCount(r) : r.objects.filter(saved).length;"
    + "\nreturn {indexedCount, indexable, nSave};";
  const saved = (o) => o.tag && (o.confidence || 0) >= cut;
  const searchMode = () => mode === "searchable";
  return new Function("saved", "searchMode", src)(saved, searchMode);
}

const objs = [
  {name: "Orion", confidence: 90, tag: true, items: 1},
  {name: "M42", confidence: 80, tag: true, items: 1},
  {name: "Faint", confidence: 80, tag: true, items: 0},      // saved by the cut but no sky label item: not indexed
  {name: "Low", confidence: 40, tag: true, items: 1},        // below the cut
  {name: "Betelgeuse", confidence: 95, tag: false, items: 1} // covered by its constellation
];

test("searchable count = saved names that have a sky label item (what Facet will index)", () => {
  const P = load("searchable", 65);
  assert.equal(P.indexedCount({objects: objs}), 2);
  assert.equal(P.nSave({objects: objs}), 2);
  assert.equal(load("searchable", 30).indexedCount({objects: objs}), 3);
});

test("tag mode still counts every saved object", () => {
  assert.equal(load("tags", 65).nSave({objects: objs}), 3);
});

test("a name used by two objects counts its items once; Facet keeps at most 200", () => {
  const P = load("searchable", 0);
  assert.equal(P.indexedCount({objects: [{name: "a", confidence: 90, tag: true, items: 2}, {name: "a", confidence: 80, tag: true, items: 2}]}), 2);
  const many = Array.from({length: 250}, (_, i) => ({name: "n" + i, confidence: 90, tag: true, items: 1}));
  assert.equal(P.indexedCount({objects: many}), 200);
});

test("the page uses non-overlapping pollers, no setInterval, and locks its action buttons when stale", () => {
  const script = html.split("<script>").slice(-1)[0];
  assert.ok(!/setInterval\(/.test(script), "no setInterval in the page script");
  assert.match(script, /makePoller\(poll, 1500\)/);
  assert.match(script, /makePoller\(pollExport, 800\)/);
  assert.match(script, /function lockActions\(\)/);
  assert.match(script, /if \(e\.transient\)/);
});

// The top status (drawStatus): run the page's own function against stub elements.
function statusPanel() {
  const B = require(path.join(__dirname, "..", "..", "astro_id", "facet", "static", "bridge.js"));
  const el = {}; const $ = (id) => (el[id] = el[id] || {hidden: false, textContent: "", className: "", title: "", disabled: false});
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const src = between("function drawStatus()", "$(\"reload\").onclick");
  const mk = new Function("B", "$", "plural", "NONCE", "S",
    "let facetTarget, loadedKey, loadedLabel, photos, curWhat, curCount, curSmart, curNote, ctxSeen, loadingNow, lastManual, scopeReq;\n"
    + src + "\nreturn {draw(s){({facetTarget, loadedKey, loadedLabel, photos, curWhat, curCount, curSmart, curNote, ctxSeen, loadingNow, lastManual, scopeReq} = Object.assign({facetTarget:null, loadedKey:'', loadedLabel:'', photos:[], curWhat:'', curCount:null, curSmart:null, curNote:'', ctxSeen:true, loadingNow:false, lastManual:null, scopeReq:null}, s)); drawStatus();}};");
  const api = mk(B, $, plural, "nonce", {});
  return {el, draw: api.draw, B};
}
const album = (id) => ({kind: "album", key: "album:" + id, album_id: id});
const six = Array.from({length: 18}, () => ({}));

test("status: nothing loaded -> one line and a Load button naming what Facet shows", () => {
  const p = statusPanel();
  p.draw({facetTarget: album(6), curWhat: "Album Astro test (fixed)", curCount: 18});
  assert.equal(p.el.stLoadedText.textContent, "Nothing loaded yet.");
  assert.equal(p.el.loadFacet.hidden, false);
  assert.equal(p.el.loadFacet.textContent, "Load Album Astro test (fixed) (18 photos)");
  assert.equal(p.el.stShowing.hidden, true);
});

test("status: loaded and it IS what Facet shows -> tick, no load button, Reload link", () => {
  const p = statusPanel();
  p.draw({facetTarget: album(6), loadedKey: "album:6", loadedLabel: "Album Astro test (fixed)", photos: six, curWhat: "Album Astro test (fixed)", curCount: 18, lastManual: [1]});
  assert.equal(p.el.stLoadedText.textContent, "Loaded: Album Astro test (fixed) · 18 photos ✓");
  assert.equal(p.el.loadFacet.hidden, true);
  assert.equal(p.el.stShowing.hidden, true);
  assert.equal(p.el.reload.hidden, false);
});

test("status: loaded, Facet moved to another album -> loaded line unchanged, 'Facet is showing', 'Load ... instead'", () => {
  const p = statusPanel();
  p.draw({facetTarget: album(7), loadedKey: "album:6", loadedLabel: "Album Astro test (fixed)", photos: six, curWhat: "Album Trip", curCount: 40});
  assert.equal(p.el.stLoadedText.textContent, "Loaded: Album Astro test (fixed) · 18 photos");
  assert.equal(p.el.stShowing.hidden, false);
  assert.equal(p.el.stShowingText.textContent, "Album Trip (40 photos)");
  assert.equal(p.el.loadFacet.textContent, "Load Album Trip instead");
  assert.equal(p.el.loadFacet.hidden, false);
  assert.equal(p.el.reload.hidden, true);
});

test("status: Facet shows nothing loadable (Albums list, Add-ons) -> say so, keep the loaded line", () => {
  const p = statusPanel();
  p.draw({facetTarget: null, loadedKey: "album:6", loadedLabel: "Album Astro test (fixed)", photos: six});
  assert.equal(p.el.stLoadedText.textContent, "Loaded: Album Astro test (fixed) · 18 photos");
  assert.equal(p.el.loadFacet.hidden, true);
  assert.equal(p.el.facetHint.textContent, "Open an album, a folder or a photo in Facet, or pick one here:");
  const q = statusPanel();
  q.draw({facetTarget: null});
  assert.equal(q.el.stLoadedText.textContent, "Nothing loaded yet.");
  assert.equal(q.el.facetHint.textContent, "Open an album, a folder or a photo in Facet, or pick one here:");
});

// ---------------------------------------------------------------- self-update (B-P8) ---
// The page's own update code (applyUpdate / restartAndWait / checkVersion), run against a scripted api/state and
// api/restart. Fake pollers and an instant sleep; `server` is what api/state answers, in order.
function updatePanel(opts) {
  const real = require(path.join(__dirname, "..", "..", "astro_id", "facet", "static", "bridge.js"));
  let clock = 0;
  const pollers = [];
  const B = Object.assign({}, real, {
    makePoller: (fn, ms) => { const p = {fn, ms, running: false, start() { this.running = true; }, stop() { this.running = false; }}; pollers.push(p); return p; },
    waitForBoot: (get, boot, o) => real.waitForBoot(get, boot, Object.assign({}, o, {sleep: async () => { clock += 600; }, now: () => clock})),
  });
  const el = {}; const $ = (id) => (el[id] = el[id] || {hidden: true, textContent: ""});
  const calls = [], stale = [], replaced = [];
  const states = (opts.states || []).slice();
  const API = {
    call: async (p, body) => {
      calls.push(p);
      if (p === "api/restart") { if (opts.restart instanceof Error) throw opts.restart; return opts.restart || {restart: true}; }
      if (p === "api/state") {
        if (!states.length) throw new Error("Failed to fetch");
        const s = states.length > 1 ? states.shift() : states[0];
        if (s instanceof Error) throw s;
        return s;
      }
      throw new Error("unexpected " + p);
    },
  };
  const location = {search: opts.search || "?nonce=abcdefgh", href: "http://h/addons/astro-id/ui/" + (opts.search || "?nonce=abcdefgh"),
                    replace: (u) => replaced.push(u)};
  const src = between("const RELOADED", "const versionPoller");
  const mk = new Function("B", "$", "API", "PAGE_VERSION", "location", "markStale",
    "let serverStale = false, updating = null, initDone = true, state = {};\n" + src
    + "\nreturn {checkVersion, get updating() { return updating; }, get state() { return state; }};");
  const page = mk(B, $, API, opts.page || "2026-11-14.2", location, (m, n) => stale.push([m, n]));
  return {page, el, calls, stale, replaced, pollers, B: real};
}
const st = (pv, u) => ({page_version: pv, update: u === null ? undefined : Object.assign({supported: true, boot: "b0", code_changed: false, busy: null, unsaved: false, disk_page_version: "2026-11-14.2"}, u)});

test("update: page newer than the add-on -> 'Updating Astro…', api/restart, wait for a new boot, no stop/open", async () => {
  const p = updatePanel({states: [st("2026-11-14.1", {boot: "b0"}), st("2026-11-14.2", {boot: "b1"})]});
  const run = p.page.checkVersion(st("2026-11-14.1", {boot: "b0", code_changed: true}));
  assert.ok(p.page.updating, "api() calls wait while the add-on restarts");
  assert.equal(p.el.update.textContent, "Updating Astro…");
  assert.equal(p.el.update.hidden, false);
  await run;
  assert.deepEqual(p.calls, ["api/restart", "api/state", "api/state"]);
  assert.equal(p.page.updating, null);
  assert.equal(p.el.update.hidden, true);
  assert.deepEqual(p.stale, []);
  assert.deepEqual(p.replaced, []);
  assert.equal(p.page.state.update.boot, "b1");
});

test("update: python-only change (same page version, code changed on disk) -> restart too", async () => {
  const p = updatePanel({states: [st("2026-11-14.2", {boot: "b1"})]});
  await p.page.checkVersion(st("2026-11-14.2", {boot: "b0", code_changed: true}));
  assert.deepEqual(p.calls, ["api/restart", "api/state"]);
  assert.deepEqual(p.stale, []);
});

test("update: a running solve -> 'Astro will update after this solve.', no restart until it ends", async () => {
  const p = updatePanel({states: [st("2026-11-14.2", {boot: "b1"})]});
  await p.page.checkVersion(st("2026-11-14.1", {code_changed: true, busy: "solve"}));
  assert.deepEqual(p.calls, []);
  assert.equal(p.el.update.textContent, "Astro will update after this solve.");
  const wait = p.pollers.find((x) => x.ms === 3000);
  assert.ok(wait.running, "asks again every 3 s");
  // the solve ended: the next check restarts
  p.calls.length = 0;
  const p2 = updatePanel({states: [st("2026-11-14.2", {boot: "b1"})]});
  await p2.page.checkVersion(st("2026-11-14.1", {code_changed: true, busy: null}));
  assert.deepEqual(p2.calls, ["api/restart", "api/state"]);
});

test("update: an export -> its own words; a refusal race (busy at api/restart) -> wait, not an error", async () => {
  const p = updatePanel({});
  await p.page.checkVersion(st("2026-11-14.1", {code_changed: true, busy: "export"}));
  assert.equal(p.el.update.textContent, "Astro will update after this export.");
  const q = updatePanel({restart: {restart: false, reason: "busy", busy: "solve"}});
  await q.page.checkVersion(st("2026-11-14.1", {code_changed: true}));
  assert.deepEqual(q.calls, ["api/restart"]);
  assert.equal(q.el.update.textContent, "Astro will update after this solve.");
  assert.deepEqual(q.stale, []);
});

test("update: this page is old too and the results are unsaved -> ask for Save to Facet first (a reload would hide them)", async () => {
  const p = updatePanel({page: "2026-11-14.1"});
  await p.page.checkVersion(st("2026-11-14.1", {code_changed: true, unsaved: true, disk_page_version: "2026-11-14.2"}));
  assert.deepEqual(p.calls, []);
  assert.equal(p.el.update.textContent, "Astro will update after you save these results to Facet.");
  // saved (or the page is current): restart, then the old page reloads itself with the nonce kept
  const q = updatePanel({page: "2026-11-14.1", states: [st("2026-11-14.2", {boot: "b1"})]});
  await q.page.checkVersion(st("2026-11-14.1", {code_changed: true, unsaved: false}));
  assert.deepEqual(q.calls, ["api/restart", "api/state"]);
  assert.deepEqual(q.replaced, ["http://h/addons/astro-id/ui/?nonce=abcdefgh&astro_reloaded=1"]);
});

test("update: the add-on is newer than the page -> the page reloads itself once, then says reopen", async () => {
  const p = updatePanel({page: "2026-11-14.1"});
  await p.page.checkVersion(st("2026-11-14.2", {}));
  assert.deepEqual(p.calls, []);
  assert.equal(p.replaced.length, 1);
  assert.match(p.replaced[0], /nonce=abcdefgh&astro_reloaded=1$/);
});

test("update: an add-on too old to restart itself -> one sentence with the two clicks", async () => {
  const p = updatePanel({});
  await p.page.checkVersion({page_version: "2026-11-14.1"});
  assert.deepEqual(p.calls, []);
  assert.equal(p.stale.length, 1);
  assert.equal(p.stale[0][0], "Astro needs one restart to update: on Facet's Add-ons page click Stop, then Open.");
});

test("update: the add-on never comes back -> the same one sentence, not a hang", async () => {
  const p = updatePanel({states: [new Error("Failed to fetch")]});
  await p.page.checkVersion(st("2026-11-14.1", {code_changed: true}));
  assert.equal(p.calls[0], "api/restart");
  assert.ok(p.calls.length > 10, "kept asking until the timeout");
  assert.equal(p.page.updating, null);
  assert.equal(p.stale.length, 1);
  assert.equal(p.stale[0][0], "Astro needs one restart to update: on Facet's Add-ons page click Stop, then Open.");
  assert.equal(p.el.update.hidden, true);
});

test("update: same code everywhere -> nothing shown, nothing called", async () => {
  const p = updatePanel({});
  await p.page.checkVersion(st("2026-11-14.2", {}));
  assert.deepEqual(p.calls, []);
  assert.deepEqual(p.stale, []);
  assert.equal(p.el.update.hidden, true);
});
