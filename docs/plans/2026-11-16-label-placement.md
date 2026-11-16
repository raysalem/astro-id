# Plan: label placement without overlaps

Goal: no label over another label, over another object's marker or past the image edge, in every renderer and in
the annotation payload. Branch: `label-placement`. Started 2026-11-16. Status: done and merged into main
2026-11-16 after review attempt 2 passed (owner approved the merge).

Owner's rule: near an image edge the text goes inward (on the right edge: text, then the circle), never past the
edge, never over another label.

## Big picture
1. Project: astro-id names what is in a night-sky or Moon photo and draws the names on a labeled copy; Facet's photo
   layer draws the same names live from the annotation payload.
2. Task: the labels collided. Seen on main @ 8c8ee5c: sky 01, "URSA MINOR" on "Little Dipper", "CASSIOPEIA" touching
   "W of Cassiopeia", "M45 Pleiades" pushed back over its own circle at the right edge, the legend box over labels;
   Moon 08, "Mare Tranquillitatis" into "Mare Crisium", "Apollo 12"/"Apollo 14", "Plato"/"Montes Teneriffe".
3. Touches: `astro_id/labels.py` (new), `overlay.py`, `export.py`, `annotations.py`, `moon.py`; tests; the example
   images and demo; `ANNOTATIONS.md`, `docs/REFERENCE.md`, `docs/TESTING.md`, `docs/DETAILS.md`.
4. Nearby: [TODO](../TODO.md) Engine item "Label placement"; the [Moon plan](2026-11-15-moon-features.md).
5. Risk: the payload is Facet's format; a change there must stay additive (Facet keeps unknown fields).

What the docs and Facet's code promise (read first): Facet's layer (`annotations.ts`, `layoutAnnotations`) centres a
`lines` label on `label_at` (x = centre, y = baseline), puts a point/circle label right of its marker and ignores
`label_at` there, sizes labels at 1.3 % of the long edge with 0.58 font sizes per character, and skips a label that
would overlap one already placed, highest score first.

## Steps
| # | Step | Files / symbols | Status |
|---|---|---|---|
| 1 | Placement routine: candidates right, left, above, below, diagonals, then pushed out by 0.6 and 1.2 font heights; inside the image; clear of placed labels, other markers (small as discs, big circles by their ring) and reserved boxes; centred names slide along their figure; short form, else hidden | `astro_id/labels.py` (`place`, `Request`, `Placement`, `Disc`, `short_forms`, `pil_measure`) | done |
| 2 | Sky overlay: layout split from drawing, legend box as an obstacle, `_label` no longer clamps after the check | `astro_id/overlay.py` (`layout`, `legend_box`, `draw_overlay`, `_label`) | done |
| 3 | Export and Moon render on the same routine | `astro_id/export.py` (`layout_items`, `lines_anchor`, `render`) | done |
| 4 | Payload: `label_at` at Facet's metric, `label_hidden`, `label_text` | `astro_id/annotations.py` (`layout_payload`, `facet_metrics`), `astro_id/moon.py` (`build_payload`) | done |
| 5 | Tests on captured real inputs of all 9 examples (no ASTAP in CI) | `tests/test_labels.py`, `tests/fixtures/labels/`, `tools/make_label_fixtures.py` | done |
| 6 | Redraw examples, hero, gallery, Moon crop, demo; `expected.json` unchanged | `examples/annotated/`, `examples/moon/annotated/`, `docs/img/` | done |
| 7 | Docs and notes | `ANNOTATIONS.md`, `docs/REFERENCE.md`, `docs/TESTING.md`, `docs/DETAILS.md`, `docs/TODO.md`, `tools/README.md`, `astro_id/README.md` | done |
| 8 | Review fixes, attempt 1 (see Review) | `annotations.py`, `export.py`, `overlay.py`, `tests/test_labels.py`, `tools/make_label_fixtures.py` | done |

Navigation: text search (`rg`).

Tests in step 5:
- Property, on every example: every placed box inside the image, no two intersect, none covers another object's
  marker or the legend. Covered for the sky overlay (with and without scores), Export / Moon render, and the payload
  at Facet's size (markers with Facet's floor of 0.4 % of the long edge, own item skipped).
- Named pairs above are drawn and apart; M45 at the right edge is text, then circle.
- Edge cases: a marker at each edge and corner goes inward; a crowd is shortened or hidden, never overlapped;
  centred labels slide and are pulled inside; short forms.
- Determinism: the same requests twice give the same boxes; the first payload layout from the pre-layout capture
  is the same twice, lays something out, and a second layout changes nothing. Equal-priority input in a different
  order is NOT promised to give the same layout (the sort is stable, so input order breaks full ties).
- A `lines` item and a point in the same unknown group at the same score sort without a TypeError.

## Decisions
- One routine for every renderer and the payload: one rule set instead of three drifting copies. Recorded in
  [DETAILS](../DETAILS.md).
- The payload is laid out with Facet's metric, not astro-id's PIL font: the boxes then match what Facet draws.
- `label_at` is added on points and circles shown by default (today's Facet ignores it there), plus `label_hidden`
  and `label_text`: additive, so no Facet change is needed. A hidden `lines` item keeps its centroid `label_at`.
- Only items shown by default and all `lines` items are laid out in the payload, default-shown ones first: placing
  every star to magnitude 6.5 would cost seconds for labels Facet does not draw by default.
- Labels may still cross stick-figure lines; only labels, markers and the legend are obstacles.

## Open for the next dev
- Facet's layer could read `label_at` / `label_hidden` / `label_text` for points and circles (a Facet change).
- Markers under the legend box stay hidden by it (as before this change).
- In a crowd a label can sit a little away from its marker (e.g. "Longomontanus" on Moon 05).
- Start in `astro_id/labels.py`; the fixtures are rebuilt with `uv run python tools/make_label_fixtures.py`.

## Review
- dev-notes-reviewer, changes needed, 2026-11-16, attempt 1. Fixes:
  - Merged origin/main (dev-notes v1.1.0 adoption); notes closed (DETAILS decisions and gotchas, TODO item, tools and
    astro_id READMEs, TESTING count); this plan put in the PLAN template; `label_short` corrected to `label_text`;
    the shuffled-input promise dropped.
  - Sort keys made one shape `(first, -score, -r or 0.0, name)` in `annotations.layout_payload`,
    `export.layout_items` and `overlay.layout`; test
    `test_lines_and_point_with_equal_priority_do_not_crash_the_sort`.
  - Payload test now also asserts no label covers another default-shown marker at Facet's size; fixtures are
    captured before `layout_payload`, and the determinism test proves the first layout.
  - `overlay.py` imports `LEVEL_BODY` from `tagpolicy`; the unused `_overlap` import left `export.py`; the sky test
    uses the obstacles `layout` returns; `ANNOTATIONS.md` says a hidden `lines` item keeps its centroid `label_at`.
- dev-notes-reviewer, pass, 2026-11-16, attempt 2, all attempt-1 findings resolved (sort keys one shape + test,
  pre-layout fixtures, marker and first-layout determinism tests, notes closed); 539 tests pass, dev-notes check 0
  errors, Moon 08 re-render byte-identical.
