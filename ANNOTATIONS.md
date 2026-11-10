# Facet photo annotations: payload v1 (astro-id ↔ Facet viewer)

A generic, plugin-written overlay layer. A plugin computes its result once (e.g. a plate solve) and stores it; the Facet viewer draws it live and filters it with the viewer's own controls (score, groups, metric sliders). Nothing is re-solved when the user moves a slider.

## Storage (Facet side)
Side table `photo_annotations` (sticky, never rewritten by a rescan):
`photo_path TEXT REFERENCES photos(path) ON DELETE CASCADE`, `source TEXT` (e.g. `astro-id`), `payload TEXT` (JSON below), `created_at TEXT`; primary key `(photo_path, source)`. Kept with the photo by the path-update triggers like other side tables.

## Payload
```json
{
  "version": 1,
  "source": "astro-id",
  "title": "Sky annotations",
  "image": {"width": 9504, "height": 6336},
  "legend": "2026-11-07 21:57 PST ±15 min · ASTAP grade A · 2452 stars",
  "groups": [
    {"id": "constellation", "label": "Constellations", "default": true},
    {"id": "planet", "label": "Planets & Moon", "default": true},
    {"id": "catalog", "label": "Messier / NGC / IC", "default": true},
    {"id": "asterism", "label": "Asterisms", "default": true},
    {"id": "star", "label": "Stars", "default": false}
  ],
  "metrics": [
    {"key": "mag", "label": "Max magnitude", "min": -5, "max": 8, "step": 0.5, "default": 6, "direction": "max"}
  ],
  "score": {"label": "Confidence", "min": 0, "max": 100, "default": 65},
  "items": [
    {"id": "uranus", "label": "Uranus", "group": "planet", "score": 100,
     "shape": "point", "x": 5123.4, "y": 2210.8, "r": 18, "values": {"mag": 5.6}},
    {"id": "m45", "label": "M45 Pleiades", "group": "catalog", "score": 99,
     "shape": "circle", "x": 4000, "y": 1800, "r": 220, "values": {"mag": 1.6}},
    {"id": "orion", "label": "Orion", "group": "constellation", "score": 100,
     "shape": "lines", "segments": [[[x1, y1], [x2, y2]], [[x3, y3], [x4, y4]]],
     "label_at": [6000, 3000]},
    {"id": "alnilam", "label": "Alnilam", "group": "star", "score": 100,
     "shape": "point", "x": 6100, "y": 3050, "r": 10, "values": {"mag": 1.7}, "member_of": "orions-belt"}
  ]
}
```

- Coordinates are full-resolution source pixels, origin top-left; the viewer scales by `displayed / image.width`.
- `shape`: `point` (marker), `circle` (radius `r`), `lines` (list of 2-point segments, already distortion-corrected by the plugin, so the viewer never needs a WCS), `label_at` for where to put a lines item's label.
- Filters the viewer applies live: item shown when its group toggle is on, `score >= score slider`, and for each metric with `direction: "max"`, `values[key] <= slider` (items without that value are not filtered by it). `member_of` items are hidden when the item they belong to is shown (optional toggle "show members").
- Labels are the plain `label`. The score appears only in a hover/focus tooltip, or on labels when the user turns on "Show scores" (default off).
- Unknown fields are ignored; `version` other than 1 is not drawn.
