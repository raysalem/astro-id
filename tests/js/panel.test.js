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
