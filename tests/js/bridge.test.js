// Bridge (postMessage v1) checks, no build step: `node --test tests/js/bridge.test.js`.
// Run from pytest by tests/test_facet_addon.py::test_bridge_js_under_node when node is installed.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const B = require(path.join(__dirname, "..", "..", "astro_id", "facet", "static", "bridge.js"));

const NONCE = "n0nce-ABCDEFGH-123";

function fakeWindows() {
  const listeners = [];
  const parent = { sent: [], postMessage(m, origin) { this.sent.push({ m, origin }); } };
  const win = { addEventListener(t, f) { if (t === "message") listeners.push(f); } };
  const deliver = (source, data) => listeners.forEach((f) => f({ source, data }));
  return { win, parent, deliver };
}

test("readNonce takes ?nonce= and refuses junk", () => {
  assert.equal(B.readNonce("?nonce=" + NONCE), NONCE);
  assert.equal(B.readNonce("?a=1&nonce=" + NONCE + "&b=2"), NONCE);
  assert.equal(B.readNonce(""), null);
  assert.equal(B.readNonce("?nonce=short"), null);
  assert.equal(B.readNonce("?nonce=has%20space%20xxxxxxx"), null);
  assert.equal(B.readNonce("?nonce=<script>alert(1)</script>"), null);
});

test("ready is posted to the parent with v, nonce and target *", () => {
  const { win, parent } = fakeWindows();
  const br = B.createBridge({ win, parent, nonce: NONCE });
  assert.equal(br.active, true);
  assert.equal(br.ready(), true);
  assert.deepEqual(parent.sent, [{ m: { type: "ready", v: 1, nonce: NONCE }, origin: "*" }]);
});

test("context is accepted only from window.parent with the matching nonce and v 1", () => {
  const { win, parent, deliver } = fakeWindows();
  const got = [];
  B.createBridge({ win, parent, nonce: NONCE, onContext: (c) => got.push(c) });
  const ctx = { type: "context", v: 1, nonce: NONCE, route: "/gallery", album_id: null, photo: null,
                selection: [], scope: { kind: "import", run_id: 12 } };
  deliver({}, ctx);                                            // another window
  deliver(parent, Object.assign({}, ctx, { nonce: "n0nce-ABCDEFGH-124" }));   // wrong nonce
  deliver(parent, Object.assign({}, ctx, { nonce: undefined }));
  deliver(parent, Object.assign({}, ctx, { v: 2 }));            // unknown version
  deliver(parent, Object.assign({}, ctx, { type: "show_photos" }));
  deliver(parent, "context");                                  // not an object
  assert.equal(got.length, 0);
  deliver(parent, ctx);
  assert.equal(got.length, 1);
  assert.deepEqual(got[0].scope, { kind: "import", run_id: "12" });
});

test("context is sanitised: unknown fields dropped, selection capped at 500", () => {
  const sel = Array.from({ length: 700 }, (_, i) => "//nas/p" + i + ".jpg");
  const c = B.sanitizeContext({ type: "context", v: 1, nonce: NONCE, route: "/", photo: "//nas/a.jpg",
                                selection: sel.concat([5, null, ""]), scope: { kind: "selection" },
                                token: "fct_secret", html: "<img onerror=x>" });
  assert.equal(c.selection.length, 500);
  assert.equal(c.token, undefined);
  assert.equal(c.html, undefined);
  assert.deepEqual(Object.keys(c).sort(), ["album_id", "photo", "route", "scope", "selection"]);
  assert.equal(B.sanitizeContext({ scope: { kind: "view", query: "x".repeat(3000) } }).scope, null);
  assert.equal(B.sanitizeContext({ scope: { kind: "delete_everything" } }).scope, null);
});

test("scope maps to the step-1 request: import run, view query, selection paths", () => {
  const base = { route: "/", album_id: null, photo: null, selection: [] };
  assert.deepEqual(B.scopeRequest(Object.assign({}, base, { scope: { kind: "import", run_id: "7" } }), "astro"),
                   { kind: "import", run_id: "7" });   // no category filter: the import's photos, all of them
  assert.equal(B.scopeRequest(Object.assign({}, base, { scope: { kind: "view", query: "?tag=moon" } })), null);
  assert.deepEqual(B.scopeRequest(Object.assign({}, base, { selection: ["a", "b"], scope: { kind: "selection" } })),
                   { kind: "selection", paths: ["a", "b"] });
  // a selection scope with nothing selected falls back to the photo being viewed
  assert.deepEqual(B.scopeRequest(Object.assign({}, base, { photo: "p", scope: { kind: "selection" } })),
                   { kind: "selection", paths: ["p"] });
  assert.equal(B.scopeRequest(Object.assign({}, base, { scope: { kind: "selection" } })), null);
  assert.equal(B.scopeRequest(Object.assign({}, base, { scope: null })), null);
  assert.equal(B.scopeRequest(null), null);
  // an unfiltered view (the whole library, or a page like Add-ons) is never a session scope
  assert.equal(B.scopeRequest(Object.assign({}, base, { scope: { kind: "view", query: "" } })), null);
  assert.equal(B.scopeRequest(Object.assign({}, base, { scope: { kind: "view", query: "?" } })), null);
  assert.equal(B.scopeRequest(Object.assign({}, base, { scope: { kind: "view", query: {} } })), null);
  const k1 = B.scopeKey({ kind: "import", run_id: "7" }), k2 = B.scopeKey({ kind: "import", run_id: "8" });
  assert.notEqual(k1, k2);
  assert.equal(B.scopeKey({ kind: "view", query: { a: 1 } }), B.scopeKey({ kind: "view", query: { a: 1 } }));
});

test("panel -> host messages: show_photos, open_photo, refresh, close", () => {
  const { win, parent } = fakeWindows();
  const br = B.createBridge({ win, parent, nonce: NONCE });
  br.showPhotos(["a", 3, "b"]);
  br.openPhoto("a");
  br.openPhoto("");
  br.refresh(["a"]);
  br.close();
  assert.deepEqual(parent.sent.map((s) => s.m), [
    { type: "show_photos", paths: ["a", "b"], v: 1, nonce: NONCE },
    { type: "open_photo", path: "a", v: 1, nonce: NONCE },
    { type: "refresh", paths: ["a"], v: 1, nonce: NONCE },
    { type: "close", v: 1, nonce: NONCE },
  ]);
  assert.ok(parent.sent.every((s) => s.origin === "*"));
});

test("without a nonce (standalone page) the bridge is inert", () => {
  const { win, parent, deliver } = fakeWindows();
  const got = [];
  const br = B.createBridge({ win, parent, nonce: null, onContext: (c) => got.push(c) });
  assert.equal(br.active, false);
  assert.equal(br.ready(), false);
  deliver(parent, { type: "context", v: 1, nonce: null, scope: { kind: "selection" } });
  assert.equal(parent.sent.length + got.length, 0);
  // top-level page (parent === window): inert as well
  const self = { addEventListener() {}, postMessage() { throw new Error("no"); } };
  assert.equal(B.createBridge({ win: self, parent: self, nonce: NONCE }).ready(), false);
});

test("api wrapper: X-Addon-Nonce on every request, no credentials, relative URLs only", async () => {
  const seen = [];
  const fetchFn = async (url, opt) => {
    seen.push({ url, opt });
    return { ok: true, status: 200, json: async () => ({ ok: 1 }), blob: async () => "BLOB" };
  };
  const api = B.makeApi(fetchFn, NONCE);
  assert.deepEqual(await api.call("api/state"), { ok: 1 });
  await api.call("api/scope", { kind: "import", run_id: "1" });
  assert.equal(await api.blob("api/thumb?path=x"), "BLOB");
  assert.equal(seen.length, 3);
  for (const s of seen) {
    assert.equal(s.opt.headers["X-Addon-Nonce"], NONCE);
    assert.equal(s.opt.credentials, "omit");
  }
  assert.equal(seen[1].opt.method, "POST");
  assert.equal(seen[1].opt.headers["Content-Type"], "application/json");
  for (const bad of ["/api/state", "http://evil/x", "//evil/x", "javascript:alert(1)"]) {
    await assert.rejects(api.call(bad), /relative URLs only/);
  }
  const failing = B.makeApi(async () => ({ ok: false, status: 403, json: async () => ({ error: "nope" }) }), NONCE);
  await assert.rejects(failing.call("api/state"), /nope/);
});

test("makeApi: Facet or the add-on restarted under the panel -> a plain 'reopen the panel' message", async () => {
  // add-on gone: Facet 404s the CORS preflight and fetch rejects with a bare TypeError
  // (one failed fetch is a hiccup, two in a row mean it is gone)
  const gone = B.makeApi(async () => { throw new TypeError("Failed to fetch"); }, NONCE);
  await assert.rejects(gone.call("api/save-config", {}), (e) => e.transient === true && !e.stale);
  await assert.rejects(gone.call("api/save-config", {}), (e) => e.stale === true && /restarted.*reopen the Astro panel/i.test(e.message));
  await assert.rejects(gone.blob("api/thumb?path=x"), /restarted/);
  // add-on back with a new panel table: this panel's nonce is stale (Facet's refusal says "detail")
  const stale = B.makeApi(async () => ({ ok: false, status: 403,
                                         json: async () => ({ detail: "Missing or stale panel nonce" }) }), NONCE);
  await assert.rejects(stale.call("api/job?after=0"), (e) => e.stale === true && /restarted.*reopen the Astro panel/i.test(e.message));
  // any other Facet refusal still shows its own detail
  const other = B.makeApi(async () => ({ ok: false, status: 400, json: async () => ({ detail: "Invalid path" }) }), NONCE);
  await assert.rejects(other.call("api/state"), /Invalid path/);
});

test("constEq compares in full", () => {
  assert.equal(B.constEq("abc", "abc"), true);
  assert.equal(B.constEq("abc", "abd"), false);
  assert.equal(B.constEq("abc", "ab"), false);
  assert.equal(B.constEq(undefined, "ab"), false);
});

test("openMap posts only the two fixed site keys, never a URL", () => {
  const { win, parent } = fakeWindows();
  const br = B.createBridge({ win, parent, nonce: NONCE });
  assert.equal(br.openMap("earth"), true);
  assert.equal(br.openMap("maps"), true);
  for (const bad of ["https://evil.example/", "toString", "__proto__", "", undefined, 1]) {
    assert.equal(br.openMap(bad), false);
  }
  assert.deepEqual(parent.sent.map((s) => s.m), [
    { type: "open_map", site: "earth", v: 1, nonce: NONCE },
    { type: "open_map", site: "maps", v: 1, nonce: NONCE },
  ]);
  assert.deepEqual(Object.keys(B.MAP_SITES).sort(), ["earth", "maps"]);
});

test("annotationView posts {type: annotation_view, view} with v and nonce (§6c)", () => {
  const { win, parent } = fakeWindows();
  const br = B.createBridge({ win, parent, nonce: NONCE });
  const view = { visible: true, groups: { constellation: true, star: false }, score: 70, metrics: { mag: 4.5 },
                 show_scores: true };
  assert.equal(br.annotationView(view), true);
  assert.deepEqual(parent.sent, [{ m: { type: "annotation_view", view, v: 1, nonce: NONCE }, origin: "*" }]);
});

test("annotation_view is sanitised: hidden by default, unknown keys and bad types dropped", () => {
  assert.deepEqual(B.sanitizeView(undefined), { visible: false });
  assert.deepEqual(B.sanitizeView({ visible: "yes", source: "other-addon", html: "<b>" }), { visible: false });
  const v = B.sanitizeView({ visible: 1, groups: { planet: true, "bad key!": true, star: "no" }, score: "70",
                             metrics: { mag: 6, evil: "x", "__proto__ x": 1 }, show_scores: "true", show_members: false });
  assert.deepEqual(v, { visible: false, groups: { planet: true }, metrics: { mag: 6 }, show_members: false });
  assert.deepEqual(B.sanitizeView({ score: null }), { visible: false, score: null });
  assert.deepEqual(B.sanitizeView({ score: Infinity }), { visible: false });
});

test("annotationView is inert without a nonce (standalone page)", () => {
  const { win, parent } = fakeWindows();
  const br = B.createBridge({ win, parent, nonce: null });
  assert.equal(br.annotationView({ visible: true }), false);
  assert.equal(parent.sent.length, 0);
});

test("facetTarget: what Facet shows, as one thing to load (selection > album > open photo's folder > import > folder filter)", () => {
  const base = { route: "/", album_id: null, photo: null, selection: [], scope: null };
  const ctx = (o) => Object.assign({}, base, o);
  // the open photo's folder, Facet's own separator kept, ending in one
  const w = B.facetTarget(ctx({ route: "/photo", photo: "\\\\nas\\Photos\\2026\\astro\\DSC07612.JPG" }));
  assert.deepEqual(w, { kind: "folder", key: "folder:\\\\nas\\Photos\\2026\\astro\\", prefix: "\\\\nas\\Photos\\2026\\astro\\", name: "astro" });
  assert.equal(B.facetTarget(ctx({ photo: "/p/night/A1.JPG" })).prefix, "/p/night/");
  // the album being viewed wins over an open-photo-less view
  assert.deepEqual(B.facetTarget(ctx({ route: "/album/6", album_id: 6, scope: { kind: "view", query: "" } })),
                   { kind: "album", key: "album:6", album_id: 6 });
  // ticked photos win
  const sel = B.facetTarget(ctx({ album_id: 6, selection: ["a", "b"], scope: { kind: "selection" } }));
  assert.equal(sel.kind, "selection"); assert.deepEqual(sel.paths, ["a", "b"]);
  // the import the panel was opened from
  assert.deepEqual(B.facetTarget(ctx({ scope: { kind: "import", run_id: "7" } })), { kind: "import", key: "import:7", run_id: "7" });
  // a gallery opened on a folder filter (query string or object, or the scope's own path_prefix)
  assert.equal(B.facetTarget(ctx({ scope: { kind: "view", query: "path_prefix=%2Fp%2Fnight&sort=date" } })).prefix, "/p/night/");
  assert.equal(B.facetTarget(ctx({ scope: { kind: "view", query: { path_prefix: "C:\\x\\astro" } } })).prefix, "C:\\x\\astro\\");
  assert.equal(B.facetTarget(ctx({ scope: { kind: "view", path_prefix: "/q/" } })).name, "q");
  // nothing specific: the whole library is never offered
  assert.equal(B.facetTarget(ctx({ scope: { kind: "view", query: "sort=date" } })), null);
  assert.equal(B.facetTarget(ctx({})), null);
  assert.equal(B.facetTarget(null), null);
  assert.equal(B.folderOfPath("noslash.jpg"), null);
});

test("a photo opened from an album: the album viewed last is offered, with the folder as the fallback", () => {
  const ctx = { route: "/photo", album_id: null, photo: "/p/night/A1.JPG", selection: [], scope: null };
  const t = B.facetTarget(ctx, 6);
  assert.equal(t.kind, "album"); assert.equal(t.album_id, 6); assert.equal(t.photo, "/p/night/A1.JPG");
  assert.deepEqual(t.folder, { kind: "folder", key: "folder:/p/night/", prefix: "/p/night/", name: "night" });
  assert.equal(B.facetTarget(ctx, null).kind, "folder");                 // no album viewed: the folder
  // an album route with an auxiliary outlet still names the album (Facet's albumIdOf misses it)
  assert.equal(B.albumOf({ route: "/album/42(aux:photo)", album_id: null }), 42);
  assert.equal(B.albumOf({ route: "/album/7", album_id: 7 }), 7);
  assert.equal(B.albumOf({ route: "/photo", album_id: null }), null);
  assert.equal(B.facetTarget({ route: "/album/42(aux:photo)", album_id: null, photo: null, selection: [], scope: null }).album_id, 42);
});

test("sanitizeContext keeps a scope's path_prefix string only", () => {
  assert.equal(B.sanitizeContext({ scope: { kind: "view", query: "", path_prefix: "/p/" } }).scope.path_prefix, "/p/");
  assert.equal("path_prefix" in B.sanitizeContext({ scope: { kind: "import", run_id: "1", path_prefix: 5 } }).scope, false);
});

test("makeApi: a transient network error is not 'restarted'; the next good answer resets the count", async () => {
  let n = 0;
  const flaky = B.makeApi(async () => {
    n += 1;
    if (n === 1 || n === 3) throw new TypeError("Failed to fetch");
    return { ok: true, status: 200, json: async () => ({ ok: n }) };
  }, NONCE);
  await assert.rejects(flaky.call("api/job?after=0"), (e) => e.transient === true && /trying again/i.test(e.message));
  assert.deepEqual(await flaky.call("api/job?after=0"), { ok: 2 });
  await assert.rejects(flaky.call("api/job?after=0"), (e) => e.transient === true);   // not two in a row
});

test("compareVersions orders the page/server contract versions", () => {
  assert.equal(B.compareVersions("2026-11-12.5", "2026-11-12.5"), 0);
  assert.equal(B.compareVersions("2026-11-12.6", "2026-11-12.5"), 1);
  assert.equal(B.compareVersions("2026-11-12.5", "2026-11-13"), -1);
  assert.equal(B.compareVersions("2026-11-12.10", "2026-11-12.9"), 1);
  assert.equal(B.compareVersions(undefined, "2026-11-12.5"), 0);
});

test("makePoller never runs two polls at once, and stops when fn returns false", async () => {
  let inflight = 0, maxInflight = 0, calls = 0;
  const p = B.makePoller(async () => {
    inflight += 1; maxInflight = Math.max(maxInflight, inflight); calls += 1;
    await new Promise((r) => setTimeout(r, 15));   // a poll slower than the interval
    inflight -= 1;
    return calls < 4;
  }, 2);
  assert.equal(p.start(), true);
  assert.equal(p.start(), false);                  // already running
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(calls, 4); assert.equal(maxInflight, 1); assert.equal(p.running, false);
  const q = B.makePoller(async () => { throw new Error("boom"); }, 2);   // a throwing fn keeps the loop alive
  q.start(); await new Promise((r) => setTimeout(r, 30)); assert.equal(q.running, true); q.stop();
  assert.equal(q.running, false);
});

test("loadState: nothing / empty / current / other / away, and the transition when Facet moves albums", () => {
  const ctx = (o) => B.sanitizeContext(Object.assign({ route: "/", album_id: null, photo: null, selection: [], scope: null }, o));
  const a6 = B.facetTarget(ctx({ route: "/album/6", album_id: 6 })), a7 = B.facetTarget(ctx({ route: "/album/7", album_id: 7 }));
  assert.equal(B.loadState(a6, ""), "nothing");
  assert.equal(B.loadState(null, ""), "empty");
  assert.equal(B.loadState(a6, a6.key), "current");
  assert.equal(B.loadState(a7, a6.key), "other");        // loaded 6, Facet now shows 7
  assert.equal(B.loadState(null, a6.key), "away");       // loaded 6, Facet on the Albums list
  assert.equal(B.loadState(a6, a6.key), "current");      // and back
});

test("nextLastAlbum: remembered for a photo opened from an album, forgotten when the user leaves albums/photos", () => {
  const ctx = (o) => B.sanitizeContext(Object.assign({ route: "/", album_id: null, photo: null, selection: [], scope: null }, o));
  assert.equal(B.nextLastAlbum(ctx({ route: "/album/6", album_id: 6 }), null), 6);
  assert.equal(B.nextLastAlbum(ctx({ route: "/photo", photo: "/p/a.jpg" }), 6), 6);       // photo opened from the album
  assert.equal(B.nextLastAlbum(ctx({ route: "/albums" }), 6), null);                       // Albums list: forgotten
  assert.equal(B.nextLastAlbum(ctx({ route: "/addons" }), 6), null);
  assert.equal(B.nextLastAlbum(ctx({ route: "/album/9", album_id: 9 }), 6), 9);            // another album replaces it
  assert.equal(B.nextLastAlbum(ctx({ route: "/photo", photo: "/p/a.jpg" }), null), null);
  // the stale memory no longer turns a folder photo into the old album
  assert.equal(B.facetTarget(ctx({ route: "/photo", photo: "/p/a.jpg" }), B.nextLastAlbum(ctx({ route: "/albums" }), 6)).kind, "folder");
});

// ---------------------------------------------------------------- self-update (B-P8) ---
const U = (u) => Object.assign({supported: true, boot: "b0", code_changed: false, busy: null, unsaved: false, disk_page_version: "2026-11-14.2"}, u);

test("updatePlan: restart when the add-on's code changed or the page is newer; never for the same code", () => {
  assert.deepEqual(B.updatePlan({page_version: "2026-11-14.2", update: U({})}, "2026-11-14.2", {}), {action: "none"});
  assert.equal(B.updatePlan({page_version: "2026-11-14.1", update: U({code_changed: true})}, "2026-11-14.2", {}).action, "restart");
  assert.equal(B.updatePlan({page_version: "2026-11-14.2", update: U({code_changed: true})}, "2026-11-14.2", {}).action, "restart");
  // two restarts that did not bring the page's code: stop trying, say the two clicks
  const p = B.updatePlan({page_version: "2026-11-14.1", update: U({code_changed: true})}, "2026-11-14.2", {restarts: 2});
  assert.equal(p.action, "manual");
  assert.equal(p.message, B.UPDATE_MANUAL);
  // ...but a python-only change that keeps "changed" does not nag
  assert.equal(B.updatePlan({page_version: "2026-11-14.2", update: U({code_changed: true})}, "2026-11-14.2", {restarts: 2}).action, "none");
});

test("updatePlan: busy -> wait with the run named; unsaved results only matter when this page will reload", () => {
  const w = B.updatePlan({page_version: "2026-11-14.1", update: U({code_changed: true, busy: "solve"})}, "2026-11-14.2", {});
  assert.deepEqual(w, {action: "wait", busy: "solve", message: "Astro will update after this solve."});
  assert.equal(B.updatePlan({page_version: "2026-11-14.1", update: U({code_changed: true, busy: "export"})}, "2026-11-14.2", {}).message,
               "Astro will update after this export.");
  // page = disk: no reload after the restart, the add-on carries the results over -> restart now
  assert.equal(B.updatePlan({page_version: "2026-11-14.1", update: U({code_changed: true, unsaved: true})}, "2026-11-14.2", {}).action, "restart");
  // page older than disk: it reloads after, which would hide them -> save first
  assert.equal(B.updatePlan({page_version: "2026-11-14.1", update: U({code_changed: true, unsaved: true})}, "2026-11-14.1", {}).busy, "unsaved");
});

test("updatePlan: add-on newer than the page -> reload once; an old add-on -> manual", () => {
  assert.deepEqual(B.updatePlan({page_version: "2026-11-15.1", update: U({})}, "2026-11-14.2", {}), {action: "reload"});
  const again = B.updatePlan({page_version: "2026-11-15.1", update: U({})}, "2026-11-14.2", {reloaded: true});
  assert.equal(again.action, "manual");
  assert.equal(again.message, B.UPDATE_REOPEN);
  // a server from before self-update: no `update` block, maybe no page_version at all
  assert.equal(B.updatePlan({}, "2026-11-14.2", {}).message, B.UPDATE_MANUAL);
  assert.equal(B.updatePlan({page_version: "2026-11-14.1"}, "2026-11-14.2", {}).action, "manual");
  assert.equal(B.updatePlan({page_version: "2026-11-14.1", update: U({supported: false})}, "2026-11-14.2", {}).action, "manual");
});

test("restartRefused maps the server's reasons", () => {
  assert.equal(B.restartRefused({restart: false, reason: "busy", busy: "export"}).message, "Astro will update after this export.");
  assert.equal(B.restartRefused({restart: false, reason: "busy", busy: "save"}).message, "Astro will update after this save.");
  assert.equal(B.restartRefused({restart: false, reason: "busy", busy: "undo"}).message, "Astro will update after this undo.");
  assert.equal(B.restartRefused({restart: false, reason: "unsaved"}).message, "Astro will update after you save these results to Facet.");
  assert.equal(B.restartRefused({restart: false, reason: "unsupported"}).action, "manual");
  assert.equal(B.restartRefused({restart: false, reason: "current"}).action, "none");
});

test("waitForBoot: waits through errors and the old process, gives up at the timeout", async () => {
  let t = 0;
  const o = {sleep: async () => { t += 500; }, now: () => t, timeoutMs: 5000};
  const seq = [new Error("x"), {update: {boot: "old"}}, {update: {boot: "new"}}];
  const got = await B.waitForBoot(async () => { const s = seq.shift(); if (s instanceof Error) throw s; return s; }, "old", o);
  assert.equal(got.update.boot, "new");
  t = 0;
  assert.equal(await B.waitForBoot(async () => ({update: {boot: "old"}}), "old", o), null);
  assert.ok(t >= 5000);
});

test("reloadUrl keeps the nonce and marks the reload once", () => {
  assert.equal(B.reloadUrl("http://h/addons/astro-id/ui/?nonce=abc"), "http://h/addons/astro-id/ui/?nonce=abc&astro_reloaded=1");
  assert.equal(B.reloadUrl("http://h/ui/?nonce=abc&astro_reloaded=1"), "http://h/ui/?nonce=abc&astro_reloaded=1");
  assert.equal(B.reloadUrl("http://h/ui/"), "http://h/ui/?astro_reloaded=1");
  assert.equal(B.reloadUrl("http://h/ui/?nonce=a#x"), "http://h/ui/?nonce=a&astro_reloaded=1#x");
});

// B-P9: the set the add-on already holds becomes the page's load variables on open/reload/restart.
test("restoredLoad: a held album -> its key, label, album, Reload body and the album as 'viewed last'", () => {
  const r = B.restoredLoad({key: "album:6", label: "Album Astro test (fixed)", album: {id: 6, name: "Astro test (fixed)"},
                            via: "photos", body: {album_id: 6}});
  assert.equal(r.loadedKey, "album:6");
  assert.equal(r.loadedLabel, "Album Astro test (fixed)");
  assert.deepEqual(r.loadedAlbum, {id: 6, name: "Astro test (fixed)"});
  assert.deepEqual(r.lastManual, [{album_id: 6}, "Album Astro test (fixed)", {id: 6, name: "Astro test (fixed)"}, "album:6"]);
  assert.equal(r.scopeReq, null);
  assert.equal(r.lastAlbumId, 6);
});

test("restoredLoad: a held scope (import) reloads through its scope; nothing held -> null", () => {
  const r = B.restoredLoad({key: "import:7", label: "Import #7", album: null, via: "scope", body: {kind: "import", run_id: "7"}});
  assert.deepEqual(r.scopeReq, {kind: "import", run_id: "7"});
  assert.equal(r.lastManual, null);
  assert.equal(r.lastAlbumId, null);
  assert.equal(B.restoredLoad(null), null);
  assert.equal(B.restoredLoad({key: ""}), null);
  assert.equal(B.restoredLoad({label: "x"}), null);
});
