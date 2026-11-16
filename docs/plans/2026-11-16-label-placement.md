# Plan: label placement without overlaps (2026-11-16)

## Goal

No label drawn over another label, over another object's marker or past the image edge, on the sky overlay
(`<name>_astro.jpg`), the Moon overlay (`<name>_moon.jpg`), the Export render, and in the annotation payload that
Facet's photo layer draws. Owner's rule: near an image edge the text goes inward (on the right edge: text, then
the circle), never past the edge, never over another label.

Visible today (main @ 8c8ee5c): sky 01, "URSA MINOR" on top of "Little Dipper", "CASSIOPEIA" touching "W of
Cassiopeia", "M45 Pleiades" pushed back over its own circle at the right edge, the legend box drawn over labels;
Moon 08, "Mare Tranquillitatis" into "Mare Crisium", "Apollo 12"/"Apollo 14", "Plato"/"Montes Teneriffe".

## What the docs promise (read first)

- `ANNOTATIONS.md`: the payload format belongs to Facet; astro-id gives `lines` items a `label_at`. Facet's layer
  (`annotations.ts`, `layoutAnnotations`) centres a `lines` label on `label_at` (x = centre, y = baseline), puts a
  point/circle label to the right of its marker, uses a font of 1.3 % of the image's long edge and a width estimate
  of 0.58 x font size per character, and skips (does not draw) a label that would overlap one already placed,
  highest score first. It ignores `label_at` on points and circles and keeps unknown fields.
- So for Facet the payload can (a) move each constellation/asterism `label_at` to a spot free of other labels at
  Facet's own label size, (b) add `label_at` to points and circles (where astro-id would put the label; used by a
  Facet that honours it, ignored by today's), and (c) add `label_hidden: true` (additive) when no spot fits.

## Approach

One shared, deterministic routine, `astro_id/labels.py`:

- Input: label requests in priority order (text, marker centre and radius, or a centred anchor with alternative
  anchors), the image size, a `measure(text)` from the font that will draw it, and obstacles (other markers, the
  legend box).
- Marker labels try, in order: right, left, above, below, the four diagonals, then the same pushed out by 0.6 and
  1.2 font heights. A box must be inside the image and must not touch any placed label box or any other marker
  (small markers as discs; large circles only along their ring). Near the right edge the right candidate fails, so
  the text goes left of the circle, etc.
- Centred labels (constellation, asterism names) try the anchor (figure centroid), small shifts, then slide along
  the figure (its vertices and segment midpoints nearest the centroid first); a centred box is clamped inside the
  image rather than rejected.
- When nothing fits, a shorter form is tried (catalog names to their designation, "M45 Pleiades" -> "M45",
  "NGC 869 h Persei Cluster" -> "NGC 869"; lunar "Mare X" -> "M. X", "Montes X" -> "Mts X", "Sinus/Lacus/Palus X"
  -> "S./L./P. X"); if that does not fit either, the marker stays and the label is hidden. Each result says
  `hidden` and `short`.
- Priority: planets/Moon/Sun, constellations, asterisms, then the rest by confidence, then size, then name.
- Text is measured with the PIL font that draws it (`textlength` + the font's ascent/descent bbox); the payload is
  measured with Facet's own metric (above), so its boxes match what Facet will draw.

## Files

- New `astro_id/labels.py` (placement), `tests/test_labels.py`, `tests/fixtures/labels/` (the label inputs of the
  5 sky and 4 Moon examples, captured from real runs by `tools/make_label_fixtures.py`, so the tests need no ASTAP).
- `astro_id/overlay.py` (sky overlay: layout split from drawing, legend box as an obstacle), `astro_id/export.py`
  (Export and Moon render), `astro_id/annotations.py` (`layout_payload`: `label_at`, `label_hidden`, `label_short`
  on the payload), `astro_id/moon.py` (payload laid out the same way).
- Docs: `ANNOTATIONS.md`, `docs/REFERENCE.md` (one paragraph each), `docs/TESTING.md` (fixture rebuild).
- Regenerated: `examples/annotated/*`, `examples/moon/annotated/*`, `docs/img/hero.jpg`, `gallery-1..3.jpg`,
  `moon-labeled.jpg` if it is a crop of an example, `demo.mp4`/`demo.gif`. `examples/expected.json` unchanged.

## Tests

- Property, on all 9 example fixtures: every placed label box inside the image, no two intersect, none covers
  another object's marker; for the sky overlay, the Export render, the Moon render and the payload (Facet metric).
- Edge cases: a marker at each edge and corner gets its label inward and inside; a marker hard against the right
  edge gets text then circle; a crowded cluster hides labels rather than overlapping; a hidden label is reported.
- Determinism: same input twice (and shuffled-equal priorities) gives identical output.
- Existing suites unchanged (payload golden: only the new label fields may differ).
