# astro-id annotation conventions (Facet photo annotations, payload v1)

**The payload format belongs to Facet.** Its spec and validator live in Facet's `db/annotations.py` (module
docstring + `validate_payload`; for now on the `feat/photo-annotations` branch, later in Facet's `docs/VIEWER.md`
and `plugins/examples/annotations.schema.json`). This file only lists what astro-id puts into that format. The
sidecar sends it with `PUT /api/photo/annotations` when Facet has that route (see `facet_plugin/README.md`).

## What astro-id writes

- `source`: `"astro-id"`; `version`: `1`; `title`: `"Sky annotations"`.
- `image`: the full-resolution JPG frame (`width`, `height`); every coordinate is in that pixel space, also when
  the solver read the RAW (`solve_source: raw`).
- `legend`: one line, e.g. `2026-11-07 21:57 PST ±15 min · ASTAP grade A · 2452 stars`.
- `groups`, in this order: `constellation` (Constellations, on), `planet` (Planets & Moon, on), `catalog`
  (Messier / NGC / IC, on), `asterism` (Asterisms, on), `star` (Stars, off).
- `metrics`: one, `{"key": "mag", "label": "Max magnitude", "min": -5, "max": 8, "step": 0.5, "default": 6,
  "direction": "max"}`; items carry `values.mag` (V magnitude) when known.
- `score`: `{"label": "Confidence", "min": 0, "max": 100, "default": 65}`; item `score` = the engine confidence.
- Items: every identified object at every level (stars to mag 6.5). Planets, Moon, Sun and stars are `point`;
  catalog objects with a size are `circle` (`r` in px); constellations and asterisms are `lines` (segments sampled
  along the sky great circle about every 1.5 degrees, projected through the SIP-corrected solution, clipped to the
  frame, rounded to 0.1 px) with `label_at`. `id` is the slugged tag; `member_of` is the `id` of the cluster or
  asterism item a star belongs to.
- Labels are plain names; the score is never in the label.
- Size: under about 500 KB (the lowest-confidence items are dropped first). Facet caps a row at 2 MB / 20 000 items.

## Top-level `quality` (astro-id, 2026-11-11)

The sidecar adds the engine's per-frame metrics as a top-level `quality` object next to `items` (fields in
`CONTRACT.md` v1.1: `detected`, `matched`, `measured`, `fwhm_px`, `elongation`, `roundness`, `background`, `noise`,
`grade`, ...). Facet keeps unknown fields and the viewer ignores it; it is data about the frame, never an overlay
item. An unsolved frame gets no annotation row (a row astro-id wrote earlier is removed).

`tests/fixtures/annotation_golden.json` is a full example (synthetic frame), validated against Facet's
`validate_payload` by `tests/test_facet_golden.py`.
