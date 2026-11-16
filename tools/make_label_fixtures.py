"""Capture the label inputs of the example runs for tests/test_labels.py (so the tests need no ASTAP).

Run from the repo root (needs ASTAP + W08 for the sky frames, DE421 for the Moon times):
    uv run python tools/make_label_fixtures.py

Writes tests/fixtures/labels/sky-<name>.json (the objects the sky overlay draws, its legend lines and size, and the
annotation payload) and moon-<name>.json (the Moon payload and the size `astro-id moon` renders it at). Payloads are
captured BEFORE `layout_payload` (lines items keep their centroid `label_at`, no label fields), so the tests run the
first layout themselves. Only what label placement reads is kept; no photo pixels.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "tests" / "fixtures" / "labels"
MOON = {"05-nasa-svs-full-moon": "2026-11-24T12:00:00+00:00", "06-nasa-svs-waxing-crescent": "2026-11-12T12:00:00+00:00",
        "07-nasa-svs-first-quarter": "2026-11-17T12:00:00+00:00", "08-nasa-svs-waxing-gibbous": "2026-11-20T06:00:00+00:00"}
OBJ_KEYS = ("name", "kind", "confidence", "tier", "x", "y", "radius_px", "mag", "level", "member_of", "taggable")


def _r(v):
    return round(v, 1) if isinstance(v, float) else v


def _write(name: str, doc: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.json"
    p.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    print(p.relative_to(ROOT), p.stat().st_size // 1024, "KB")


def _no_layout():
    """Capture payloads as built, before label layout (restored by the caller)."""
    from astro_id import annotations

    orig = annotations.layout_payload
    annotations.layout_payload = lambda p: p
    return lambda: setattr(annotations, "layout_payload", orig)


def sky() -> None:
    from PIL import Image

    from astro_id import cli, overlay, pipeline

    seen: dict[str, dict] = {}
    orig = pipeline.draw_overlay

    def capture(src, out, width_full, objects, legend_lines, width=3000, **kw):
        with Image.open(src) as im:
            w, h = im.size
        k = min(1.0, width / w)
        seen[Path(src).stem] = {
            "size": [round(w * k) if k < 1 else w, round(h * k) if k < 1 else h], "width_full": width_full,
            "legend_lines": legend_lines,
            "objects": [{**{a: _r(getattr(o, a, None)) for a in OBJ_KEYS},
                         "figure": [[_r(float(v)) for v in seg] for seg in (o.figure or [])]} for o in objects]}
        return orig(src, out, width_full, objects, legend_lines, width=width, **kw)

    pipeline.draw_overlay = capture
    restore = _no_layout()
    with tempfile.TemporaryDirectory() as td:
        res = Path(td) / "results.json"
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["solve", str(ROOT / "examples"), "--json", "--no-fallback", "--no-quality",
                           "--overlay-dir", td])
        assert rc == 0
        doc = json.loads(buf.getvalue())
    pipeline.draw_overlay = orig
    restore()
    del overlay, res
    for r in doc["results"]:
        stem = Path(r["file"]).stem
        _write(f"sky-{stem}", {**seen[stem], "payload": r["annotations"]})


def moon() -> None:
    from astro_id import moon as M

    restore = _no_layout()
    for name, t in MOON.items():
        res = M.analyse(ROOT / "examples" / "moon" / f"{name}.jpg", t=datetime.fromisoformat(t), north_angle=0.0)
        width = min(3000, max(1200, res.width))
        k = min(1.0, width / res.width)
        _write(f"moon-{name}", {"size": [round(res.width * k), round(res.height * k)], "payload": res.annotations})
    restore()


if __name__ == "__main__":
    sky()
    moon()
