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

test("one word for astro-id's per-object output: object tags (no 'searchable names' or 'Astro tags')", () => {
  const ui = html.replace(/<script>[\s\S]*?<\/script>/g, "");
  assert.match(ui, /Save object tags/);
  assert.match(ui, /<dt>Object tags<\/dt>/);
  assert.doesNotMatch(ui, /searchable name|Make names searchable|Astro tags/i);
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

// ------------------------------------------------- Undo what Astro saved (api/undo) ---
// The page's own undo block against stub elements and a scripted api/undo: dry run -> one inline confirmation line ->
// Confirm -> the save line reports what was removed, Facet refreshes, the sky-label controls re-read.
function undoPanel(opts) {
  const el = {};
  const $ = (id) => (el[id] = el[id] || {hidden: id === "undoBox" || id === "undoGo", textContent: "", innerHTML: "", disabled: false, checked: id === "undoScopeTicked"});
  const pagePlural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
  const esc = (s) => String(s ?? "");
  const calls = [], refreshed = [], sky = [];
  const api = async (p, body) => {
    calls.push([p, body]);
    if (p !== "api/undo") throw new Error("unexpected " + p);
    const a = body.dry_run ? opts.dry : opts.done;
    if (a instanceof Error) throw a;
    return a;
  };
  const bridge = {refresh: (paths) => refreshed.push(paths)};
  const src = between("/* ---- undo (start)", "/* ---- undo (end) */");
  const mk = new Function("$", "api", "esc", "plural", "bridge", "S", "skyContext",
    "let state = S.state, photos = S.photos, review = S.review || [], toSave = new Set(S.toSave || []), serverStale = !!S.stale,"
    + " lastCtx = {photo: S.photos[0] && S.photos[0].path}, skyPhoto = 'x';\n"
    + "const selected = () => photos.filter((p) => p._on).map((p) => p.path);\n" + src
    + "\nreturn {drawUndo, undoAsk, undoConfirm, undoQuestion, undoDone, undoMark, get pending() { return undoPending; }};");
  const page = mk($, api, esc, pagePlural, bridge, opts, (p) => sky.push(p));
  return {page, el, calls, refreshed, sky};
}
const uPhotos = (led) => ["a.jpg", "b.jpg", "c.jpg"].map((p, i) => ({path: p, _on: i < 2, ledger: led[i] ? {status: "solved"} : null}));
const dry = {dry_run: true, photos: 3, object_tags: 32, status_tags: 3, sky_labels: 3, errors: 0,
             not_removed: {yours: 0, other_writer: 0, failed: 0, no_route: 0, total: 0}, reports: []};

test("undo: the confirmation line, word for word", () => {
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 1])});
  assert.equal(p.page.undoQuestion(dry), "Remove 32 object tags, 3 status tags and the sky labels from 3 photos? Your own tags stay.");
  assert.equal(p.page.undoQuestion({photos: 1, object_tags: 0, status_tags: 1, sky_labels: 0}), "Remove 1 status tag from 1 photo? Your own tags stay.");
  assert.equal(p.page.undoQuestion({photos: 2, object_tags: 0, status_tags: 2, sky_labels: 2}), "Remove 2 status tags and the sky labels from 2 photos? Your own tags stay.");
  assert.equal(p.page.undoQuestion({photos: 0, object_tags: 0, status_tags: 0, sky_labels: 0}), null);
});

test("undo: nothing recorded for the loaded set -> disabled with the reason; read-only -> its own reason", () => {
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([0, 0, 0])});
  p.page.drawUndo();
  assert.equal(p.el.undoBtn.disabled, true);
  assert.equal(p.el.undoWhy.textContent, "Nothing Astro saved is recorded for the loaded photos.");
  assert.equal(p.el.undoRow.hidden, false);
  const q = undoPanel({state: {can_write: false}, photos: uPhotos([1, 0, 0])});
  q.page.drawUndo();
  assert.equal(q.el.undoBtn.disabled, true);
  assert.equal(q.el.undoWhy.textContent, "Read-only: Astro has no Facet credential.");
  const r = undoPanel({state: {can_write: true}, photos: uPhotos([0, 1, 0])});
  r.page.drawUndo();
  assert.equal(r.el.undoBtn.disabled, false);
  assert.equal(r.el.undoWhy.textContent, "");
  const s = undoPanel({state: {can_write: true}, photos: []});
  s.page.drawUndo();
  assert.equal(s.el.undoRow.hidden, true, "nothing loaded: no undo line at all");
  // saved in this session (not in the listing) enables it; undone in this session disables it again
  r.page.undoMark.set("b.jpg", false); r.page.drawUndo();
  assert.equal(r.el.undoBtn.disabled, true);
  r.page.undoMark.set("a.jpg", true); r.page.drawUndo();
  assert.equal(r.el.undoBtn.disabled, false);
});

test("undo: click -> dry run of the ticked photos -> one line with Confirm / Cancel; nothing written yet", async () => {
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 1]), dry});
  p.page.drawUndo();
  p.el.undoBtn.onclick();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(p.calls, [["api/undo", {paths: ["a.jpg", "b.jpg"], dry_run: true}]], "ticked photos by default");
  assert.equal(p.el.undoBox.hidden, false);
  assert.equal(p.el.undoQ.textContent, "Remove 32 object tags, 3 status tags and the sky labels from 3 photos? Your own tags stay.");
  assert.equal(p.el.undoGo.hidden, false);
  // Cancel: closed, nothing sent
  p.el.undoCancel.onclick();
  assert.equal(p.el.undoBox.hidden, true);
  assert.equal(p.page.pending, null);
  assert.equal(p.calls.length, 1);
});

test("undo: 'everything loaded' asks for every loaded photo; after a solve 'ticked' = the Results ticks", async () => {
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 1]), dry});
  p.el.undoScopeAll.checked = true; p.el.undoScopeTicked.checked = false;
  await p.page.undoAsk();
  assert.deepEqual(p.calls[0][1].paths, ["a.jpg", "b.jpg", "c.jpg"]);
  const q = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 1]), review: [{path: "c.jpg"}], toSave: ["c.jpg"], dry});
  await q.page.undoAsk();
  assert.deepEqual(q.calls[0][1].paths, ["c.jpg"]);
});

test("undo: Confirm -> the same paths, the save line says what was removed, Facet refreshes, sky labels re-read", async () => {
  const done = Object.assign({}, dry, {dry_run: false, not_removed: {yours: 2, other_writer: 0, failed: 0, no_route: 0, total: 2},
    reports: [{path: "a.jpg", changed: true, not_removed: []}, {path: "b.jpg", changed: true, not_removed: []}]});
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 0]), dry, done});
  await p.page.undoAsk();
  await p.page.undoConfirm();
  assert.deepEqual(p.calls[1], ["api/undo", {paths: ["a.jpg", "b.jpg"]}]);
  assert.equal(p.el.saveinfo.innerHTML.includes("Removed 32 object tags, 3 status tags and the sky labels from 3 photos."), true);
  assert.match(p.el.saveinfo.innerHTML, /2 old tags written outside the add-on stay/);
  assert.match(p.el.saveinfo.innerHTML, /Save to Facet writes them again/);
  assert.deepEqual(p.refreshed, [["a.jpg", "b.jpg"]]);
  assert.deepEqual(p.sky, ["a.jpg"]);
  assert.equal(p.el.saveFacet.textContent, "Save to Facet");
  assert.equal(p.el.undoBtn.disabled, true, "nothing recorded any more for the loaded photos");
  assert.equal(p.el.undoWhy.textContent, "Nothing Astro saved is recorded for the loaded photos.");
});

test("undo: dry run finds nothing -> says so, no Confirm; an error shows, no Confirm", async () => {
  const none = Object.assign({}, dry, {photos: 0, object_tags: 0, status_tags: 0, sky_labels: 0});
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 1]), dry: none});
  await p.page.undoAsk();
  assert.equal(p.el.undoQ.textContent, "Nothing Astro saved is recorded for the ticked photos.");
  assert.equal(p.el.undoGo.hidden, true);
  await p.page.undoConfirm();
  assert.equal(p.calls.length, 1, "Confirm without a dry-run answer sends nothing");
  const q = undoPanel({state: {can_write: true}, photos: uPhotos([1, 1, 1]), dry: new Error("a plate solve is running: try again when it ends")});
  await q.page.undoAsk();
  assert.match(q.el.undoQ.innerHTML, /a plate solve is running/);
  assert.equal(q.el.undoGo.hidden, true);
});

test("undo: a removal Facet did not answer stays undoable (Undo again)", async () => {
  const done = Object.assign({}, dry, {dry_run: false, photos: 1, object_tags: 0, status_tags: 0, sky_labels: 1,
    not_removed: {yours: 0, other_writer: 0, failed: 1, no_route: 0, total: 1},
    reports: [{path: "a.jpg", changed: true, not_removed: [{tag: "plate-solved", why: "failed"}]}]});
  const p = undoPanel({state: {can_write: true}, photos: uPhotos([1, 0, 0]), dry, done});
  await p.page.undoAsk();
  await p.page.undoConfirm();
  assert.match(p.el.saveinfo.innerHTML, /1 not removed \(Facet did not answer: Undo again\)/);
  assert.equal(p.el.undoBtn.disabled, false);
});

test("undo: the control is a quiet link below the save line, not beside Save to Facet; Help names it", () => {
  const s4 = between('<details class="step" id="s4">', "</details>");
  const save = s4.indexOf('id="saveFacet"'), info = s4.indexOf('id="saveinfo"'), undo = s4.indexOf('id="undoBtn"');
  assert.ok(save > 0 && info > save && undo > info, "undo comes after the save line");
  assert.match(s4, /<button id="undoBtn" class="link small" disabled>Undo what Astro saved&hellip;<\/button>/);
  assert.match(s4, /the ticked photos/);
  assert.match(s4, /everything loaded/);
  const help = between('<h2 id="h-safe">', '<button id="helpBack2">');
  assert.match(help, /Undo what Astro saved/);
  assert.match(help, /object tags, the status tags and the sky labels/);
  const script = html.split("<script>").slice(-1)[0];
  assert.doesNotMatch(script, /\bconfirm\(/, "no browser confirm() in a sandboxed iframe");
});
