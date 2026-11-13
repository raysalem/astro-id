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
  const gone = B.makeApi(async () => { throw new TypeError("Failed to fetch"); }, NONCE);
  await assert.rejects(gone.call("api/save-config", {}), /restarted.*reopen the Astro panel/i);
  await assert.rejects(gone.blob("api/thumb?path=x"), /restarted/);
  // add-on back with a new panel table: this panel's nonce is stale (Facet's refusal says "detail")
  const stale = B.makeApi(async () => ({ ok: false, status: 403,
                                         json: async () => ({ detail: "Missing or stale panel nonce" }) }), NONCE);
  await assert.rejects(stale.call("api/job?after=0"), /restarted.*reopen the Astro panel/i);
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

test("sanitizeContext keeps a scope's path_prefix string only", () => {
  assert.equal(B.sanitizeContext({ scope: { kind: "view", query: "", path_prefix: "/p/" } }).scope.path_prefix, "/p/");
  assert.equal("path_prefix" in B.sanitizeContext({ scope: { kind: "import", run_id: "1", path_prefix: 5 } }).scope, false);
});
