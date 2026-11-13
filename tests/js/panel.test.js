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
