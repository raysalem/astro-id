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

test("object tag count = saved objects that have a sky label item (what Facet will index)", () => {
  const P = load("searchable", 65);
  assert.equal(P.indexedCount({objects: objs}), 2);
  assert.equal(P.nSave({objects: objs}), 2);
  assert.equal(load("searchable", 30).indexedCount({objects: objs}), 3);
});

test("older Facet (plain-tag fallback) still counts every saved object tag", () => {
  assert.equal(load("tags", 65).nSave({objects: objs}), 3);
});

test("an object name used twice counts its items once; Facet keeps at most 200", () => {
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

test("update: the add-on never comes back -> one accurate sentence, not a hang", async () => {
  const p = updatePanel({states: [new Error("Failed to fetch")]});
  await p.page.checkVersion(st("2026-11-14.1", {code_changed: true}));
  assert.equal(p.calls[0], "api/restart");
  assert.ok(p.calls.length > 10, "kept asking until the timeout");
  assert.equal(p.page.updating, null);
  assert.equal(p.stale.length, 1);
  assert.equal(p.stale[0][0], "Astro did not come back after updating: on Facet's Add-ons page click Stop, then Open.");
  assert.equal(p.stale[0][1], "", "no second, contradicting note");
  assert.equal(p.el.update.hidden, true);
});

// ------------------------------------------------------------- B-P10: safe self-update ---
const FAILED = "An update to Astro is not ready yet; still running the previous version.";
const BROKEN = "An update to Astro did not start; Astro will try again as soon as its files change.";
const bad = {reason: "api/state answered HTTP 500: AttributeError: 'App' object has no attribute 'loaded_summary'", at: "2026-11-15T10:00:00"};

test("B-P10: the new code failed its trial run -> one calm line, no restart, nothing locked, asks again every 10 s", async () => {
  const p = updatePanel({});
  await p.page.checkVersion(st("2026-11-14.2", {update_failed: bad}));
  assert.deepEqual(p.calls, [], "no api/restart onto code that cannot serve");
  assert.equal(p.el.update.textContent, FAILED);
  assert.equal(p.el.update.hidden, false);
  assert.deepEqual(p.stale, [], "no Stop/Open, no locked buttons");
  assert.ok(!/Stop|Open/.test(p.el.update.textContent));
  const slow = p.pollers.find((x) => x.ms === 10000);
  assert.ok(slow && slow.running, "keeps an eye on it");
  // also when this page is newer than the running add-on (the incident: page and server both edited)
  const q = updatePanel({page: "2026-11-15.2"});
  await q.page.checkVersion(st("2026-11-15.1", {update_failed: bad}));
  assert.deepEqual(q.calls, []);
  assert.equal(q.el.update.textContent, FAILED);
  assert.deepEqual(q.stale, []);
});

test("B-P10: files fixed -> the line goes while it is tried, then the update runs by itself", async () => {
  const p = updatePanel({states: [st("2026-11-14.2", {boot: "b0", pending: true}), st("2026-11-14.2", {boot: "b0", code_changed: true}), st("2026-11-14.2", {boot: "b1"})]});
  await p.page.checkVersion(st("2026-11-14.2", {update_failed: bad}));
  assert.equal(p.el.update.textContent, FAILED);
  const slow = p.pollers.find((x) => x.ms === 10000), fast = p.pollers.find((x) => x.ms === 3000);
  assert.equal(await slow.fn(), false, "pending: the slow poller hands over");     // api/state -> pending
  assert.equal(p.el.update.hidden, true, "nothing shown while the new code settles and is tried");
  assert.ok(fast.running);
  assert.equal(await fast.fn(), false);                                              // api/state -> ready: restart
  assert.deepEqual(p.calls, ["api/state", "api/state", "api/restart", "api/state"]);
  assert.equal(p.page.state.update.boot, "b1");
  assert.equal(p.el.update.hidden, true);
  assert.deepEqual(p.stale, []);
});

test("B-P10: new code still settling (mid-edit) -> nothing shown, no restart, asks again in 3 s", async () => {
  const p = updatePanel({page: "2026-11-15.2"});
  await p.page.checkVersion(st("2026-11-15.1", {pending: true}));
  assert.deepEqual(p.calls, []);
  assert.equal(p.el.update.hidden, true);
  assert.deepEqual(p.stale, [], "a newer page waits for the code instead of saying Stop/Open");
  assert.ok(p.pollers.find((x) => x.ms === 3000).running);
});

test("B-P10: api/restart refuses (not ready / failed) -> the matching line, not an error", async () => {
  const p = updatePanel({restart: {restart: false, reason: "update_failed", update_failed: bad}});
  await p.page.checkVersion(st("2026-11-14.2", {code_changed: true}));
  assert.deepEqual(p.calls, ["api/restart"]);
  assert.equal(p.el.update.textContent, FAILED);
  assert.deepEqual(p.stale, []);
  const q = updatePanel({restart: {restart: false, reason: "pending"}});
  await q.page.checkVersion(st("2026-11-14.2", {code_changed: true}));
  assert.equal(q.el.update.hidden, true);
  assert.ok(q.pollers.find((x) => x.ms === 3000).running);
});

test("B-P10: passed its trial but stopped after the switch -> the keeper answers; its line, no Stop/Open, no 45 s wait", async () => {
  const fb = {page_version: "2026-11-14.2", fallback: true, update: {supported: true, fallback: true, boot: "fallback-1", code_changed: false, pending: false, update_failed: bad}};
  const p = updatePanel({states: [fb]});
  await p.page.checkVersion(st("2026-11-14.2", {boot: "b0", code_changed: true}));
  assert.deepEqual(p.calls, ["api/restart", "api/state"], "the first answer after the restart is the keeper's");
  assert.equal(p.el.update.textContent, BROKEN);
  assert.deepEqual(p.stale, []);
  assert.ok(p.pollers.find((x) => x.ms === 10000).running, "tries again when the files change");
});

test("update: same code everywhere -> nothing shown, nothing called", async () => {
  const p = updatePanel({});
  await p.page.checkVersion(st("2026-11-14.2", {}));
  assert.deepEqual(p.calls, []);
  assert.deepEqual(p.stale, []);
  assert.equal(p.el.update.hidden, true);
});

test("one word for astro-id's per-object output: object tags (no 'searchable names' or 'Astro tags')", () => {
  const ui = html.replace(/<script>[\s\S]*?<\/script>/g, "");
  assert.match(ui, /Save object tags/);
  assert.match(ui, /<dt>Object tags<\/dt>/);
  assert.doesNotMatch(ui, /searchable name|Make names searchable|Astro tags/i);
});

test("B-P11: the save line says when tags were left because Facet does not say who wrote them", () => {
  assert.match(html, /d\.tags_unknown_owner \?/);
  assert.match(html, /this Facet does not say who wrote them, so they may be yours/);
});

// ------------------------------------------------- B-P9: a held set on open / reload / restart ---
// The page's own restoreLoaded + drawStatus against a scripted api/loaded: the first status line must be
// "Loaded: ... ✓" on the album page, never "Nothing loaded yet." for a set the add-on still holds.
function restorePanel(server) {
  const B = require(path.join(__dirname, "..", "..", "astro_id", "facet", "static", "bridge.js"));
  const el = {}; const $ = (id) => (el[id] = el[id] || {hidden: false, textContent: "", className: "", title: "", disabled: false});
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const calls = [];
  const api = async (p) => { calls.push(p); if (p !== "api/loaded") throw new Error("unexpected " + p); return server.loaded; };
  const src = between("async function restoreLoaded()", "function onContext(") + between("function drawStatus()", "$(\"reload\").onclick");
  const mk = new Function("B", "$", "plural", "NONCE", "api", "S",
    "let state = S.state, facetTarget = null, loadedKey = '', loadedLabel = '', loadedAlbum = null, photos = [], curWhat = '', curCount = null,"
    + " curSmart = null, curNote = '', ctxSeen = true, loadingNow = false, lastManual = null, scopeReq = null, lastAlbumId = null;\n"
    + "function loaded(d) { photos = d.photos; }\n" + src
    + "\nreturn {restoreLoaded, drawStatus, show(t, what, n) { facetTarget = t; curWhat = what; curCount = n; drawStatus(); },"
    + " get v() { return {loadedKey, loadedLabel, loadedAlbum, lastManual, scopeReq, lastAlbumId, photos}; }};");
  return {page: mk(B, $, plural, "nonce", api, server), el, calls};
}
const heldAlbum = {key: "album:6", label: "Album Astro test (fixed)", album: {id: 6, name: "Astro test (fixed)"}, via: "photos", body: {album_id: 6}};

test("B-P9: panel opened on the album the add-on holds -> 'Loaded: Album ... · 18 photos ✓' at once, no Load button", async () => {
  const p = restorePanel({state: {loaded: {key: "album:6", label: heldAlbum.label, album: heldAlbum.album, count: 18}},
                          loaded: {photos: six, folders: {}, loaded: heldAlbum}});
  await p.page.restoreLoaded();
  p.page.drawStatus();                                   // init draws before any Facet context arrives
  assert.equal(p.el.stLoadedText.textContent, "Loaded: Album Astro test (fixed) · 18 photos");
  p.page.show(album(6), "Album Astro test (fixed)", 18); // the album page's context
  assert.equal(p.el.stLoadedText.textContent, "Loaded: Album Astro test (fixed) · 18 photos ✓");
  assert.equal(p.el.loadFacet.hidden, true);
  assert.equal(p.el.reload.hidden, false, "Reload works for a restored set");
  assert.equal(p.page.v.lastAlbumId, 6, "a photo of the album opened next is offered as that album");
  // Facet moved on: the c-state pair, the loaded line unchanged
  p.page.show(album(7), "Album Trip", 40);
  assert.equal(p.el.stLoadedText.textContent, "Loaded: Album Astro test (fixed) · 18 photos");
  assert.equal(p.el.loadFacet.textContent, "Load Album Trip instead");
});

test("B-P9: nothing held (fresh add-on) -> no api/loaded call, 'Nothing loaded yet.'", async () => {
  const p = restorePanel({state: {loaded: null}, loaded: null});
  await p.page.restoreLoaded();
  p.page.show(album(6), "Album Astro test (fixed)", 18);
  assert.deepEqual(p.calls, []);
  assert.equal(p.el.stLoadedText.textContent, "Nothing loaded yet.");
});

test("B-P9: init restores the held set before the first status line and before contexts are handled", () => {
  const init = between("async function init()", "function onContext(");
  const r = init.indexOf("await restoreLoaded()"), d = init.indexOf("initDone = true"), s = init.indexOf("drawStatus();");
  assert.ok(r > 0 && r < d && d < s, "restoreLoaded -> initDone -> drawStatus");
  assert.match(html, /api\("api\/photos", Object\.assign\(\{\}, body, \{loaded: \{key: key, label: label, album: album\}\}\)\)/);
  assert.match(html, /loaded: \{key: B\.scopeKey\(req\), label: scopeLabel\(req\), album: null\}/);
  assert.doesNotMatch(html.split("<main>")[1].split("</main>")[0], /<b id="stLoadedText">Nothing loaded yet\.<\/b>/,
    "the static placeholder must not claim nothing is loaded before init knows");
});
