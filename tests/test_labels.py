"""Label placement: no label over another label or another object's marker, none past the image edge.

The example fixtures (tests/fixtures/labels, rebuilt by tools/make_label_fixtures.py) are the real inputs of the
5 sky and 4 Moon example runs, so the property is checked on what the README shows, without ASTAP."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from astro_id import annotations as A
from astro_id import export as E
from astro_id import labels as L
from astro_id import overlay as O

FIX = Path(__file__).parent / "fixtures" / "labels"
SKY = sorted(FIX.glob("sky-*.json"))
MOON = sorted(FIX.glob("moon-*.json"))


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _fonts(size):
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)), "RGBA")
    fs = max(14, size[0] // 120)
    font, small = O._font(fs), O._font(int(fs * 0.85))
    return d, fs, font, small


def _check(placed, size, discs, extra=()):
    """Every placed box inside the image, no two intersect, none over a foreign marker or a reserved box."""
    boxes = [(p.key, p.box) for p in placed if p.box is not None]
    W, H = size
    for k, b in boxes:
        assert 0 <= b[0] and 0 <= b[1] and b[2] <= W and b[3] <= H, (k, b, size)
        assert not any(L.overlap(b, e) for e in extra), (k, b)
        bad = [c for c in discs if c.key != k and L.hits_disc(b, c)]
        assert not bad, (k, b, bad[:1])
    for i, (ka, a) in enumerate(boxes):
        for kb, b in boxes[i + 1:]:
            assert not L.overlap(a, b), (ka, a, kb, b)


def _sky_layout(doc, scores=False):
    size = tuple(doc["size"])
    d, fs, font, small = _fonts(size)
    objs = [SimpleNamespace(**o) for o in doc["objects"]]
    s = size[0] / doc["width_full"]
    entries = doc["legend_lines"] + ["", "high >= 80", "likely 65-79", "candidate < 65"]
    lbox = O.legend_box(d, font, fs, size, entries)
    discs: list = []
    marks, labs = O.layout(objs, s, size, fs, L.pil_measure(d, font), L.pil_measure(d, small), scores, taken=[lbox],
                           obstacles=discs)
    return size, fs, objs, marks, labs, lbox, discs


@pytest.mark.parametrize("path", SKY, ids=lambda p: p.stem)
@pytest.mark.parametrize("scores", [False, True])
def test_sky_overlay_labels_never_overlap(path, scores):
    doc = _load(path)
    size, fs, objs, marks, labs, lbox, _ = _sky_layout(doc, scores)
    placed = [p for p, _, _ in labs]
    # own marker excluded: rebuild the discs with the request keys the layout used
    discs = []
    for n, o in enumerate(objs):
        for x, y, r, *_ in marks:
            if o.x is not None and abs(o.x * size[0] / doc["width_full"] - x) < 1e-6 \
                    and abs(o.y * size[0] / doc["width_full"] - y) < 1e-6:
                discs.append(O.ring_obstacle(x, y, r, fs, key=n))
                break
    _check(placed, size, discs, extra=[lbox])
    assert placed and sum(p.box is not None for p in placed) >= 0.8 * len(placed)


def test_sky_named_overlaps_are_gone():
    """The pairs reported on the README examples: each drawn, apart."""
    doc = _load(FIX / "sky-01-cassiopeia-perseus.json")
    _, _, objs, _, labs, _, _ = _sky_layout(doc)
    by = {objs[p.key].name: p for p, _, _ in labs}
    for a, b in (("Ursa Minor", "Little Dipper"), ("Cassiopeia", "W of Cassiopeia")):
        assert by[a].box and by[b].box and not L.overlap(by[a].box, by[b].box)
    pleiades = by["M45 Pleiades"]
    m = next(o for o in objs if o.name == "M45 Pleiades")
    assert pleiades.box[2] <= m.x * doc["size"][0] / doc["width_full"]       # text, then circle, at the right edge


def _render_layout(payload, size, view):
    d, fs, font, small = _fonts(size)
    items = E.visible_items(payload, E.settings(payload, view))
    s = size[0] / payload["image"]["width"]

    def text(it, upper=False):
        n = str(it.get("label"))
        return n.upper() if upper else n

    lbox = O.legend_box(d, font, fs, size, [payload.get("legend") or "x"])
    discs: list = []
    marks, labs = E.layout_items(items, s, size, fs, L.pil_measure(d, font), L.pil_measure(d, small), text,
                                 taken=[lbox], obstacles=discs)
    assert len(discs) == len(marks)
    return [p for p, _, _ in labs], discs, lbox, items


@pytest.mark.parametrize("path", SKY + MOON, ids=lambda p: p.stem)
def test_export_and_moon_render_labels_never_overlap(path):
    doc = _load(path)
    view = {"score": 0} if path.stem.startswith("moon") else None
    placed, discs, lbox, items = _render_layout(doc["payload"], tuple(doc["size"]), view)
    _check(placed, tuple(doc["size"]), discs, extra=[lbox])
    if items:
        assert sum(p.box is not None for p in placed) >= 0.6 * len(placed)


def test_moon_named_overlaps_are_gone():
    doc = _load(FIX / "moon-08-nasa-svs-waxing-gibbous.json")
    placed, _, _, items = _render_layout(doc["payload"], tuple(doc["size"]), {"score": 0})
    by = {items[p.key]["label"]: p for p in placed}
    for a, b in (("Mare Tranquillitatis", "Mare Crisium"), ("Apollo 12", "Apollo 14"), ("Plato", "Montes Teneriffe")):
        for k in (a, b):
            assert k in by
        if by[a].box and by[b].box:
            assert not L.overlap(by[a].box, by[b].box)
    assert by["Mare Tranquillitatis"].box and by["Mare Crisium"].box and by["Apollo 12"].box and by["Apollo 14"].box


def _facet_boxes(payload):
    fs, measure, _ = A.facet_metrics(payload)
    out = []
    for n, it in enumerate(payload["items"]):
        if it.get("label_hidden") or "label_at" not in it:
            continue
        w, _ = measure(it.get("label_text") or str(it.get("label")))
        x, y = it["label_at"]
        out.append((it["id"], (x - w / 2, y - fs, x + w / 2, y + 0.25 * fs)))
    return out


@pytest.mark.parametrize("path", SKY + MOON, ids=lambda p: p.stem)
def test_payload_label_at_never_overlap_at_facet_size(path):
    p = A.layout_payload(copy.deepcopy(_load(path)["payload"]))
    W, H = p["image"]["width"], p["image"]["height"]
    boxes = _facet_boxes(p)
    assert boxes
    for i, (ka, a) in enumerate(boxes):
        assert -0.2 <= a[0] and -0.2 <= a[1] and a[2] <= W + 0.2 and a[3] <= H + 0.2, (ka, a)
        for kb, b in boxes[i + 1:]:
            # rounding to 0.1 px may make two touching boxes share an edge
            sh = (b[0] + 0.1, b[1] + 0.1, b[2] - 0.1, b[3] - 0.1)
            assert not L.overlap(a, sh), (ka, a, kb, b)
    shown = {id(it) for it in E.visible_items(p, E.settings(p, None))}
    for it in p["items"]:
        if id(it) in shown or it.get("shape") == "lines":
            assert "label_at" in it or it.get("label_hidden") is True, it["id"]


def test_payload_layout_is_deterministic_and_stable():
    raw = _load(FIX / "sky-04-orion-rising.json")["payload"]
    a = A.layout_payload(copy.deepcopy(raw))
    b = A.layout_payload(A.layout_payload(copy.deepcopy(raw)))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------- the routine itself

def _measure(text):
    return 10.0 * len(text), 16.0


SIZE = (400, 300)


@pytest.mark.parametrize("x,y", [(398, 150), (2, 150), (200, 2), (200, 298), (1, 1), (399, 1), (1, 299), (399, 299),
                                 (395, 10), (5, 290)])
def test_marker_at_each_edge_and_corner_goes_inward(x, y):
    p = L.place([L.Request("a", "Pleiades", x, y, 8)], SIZE, _measure, [L.Disc(x, y, 9, key="a")])[0]
    assert p.box is not None and not p.hidden
    assert L.inside(p.box, *SIZE)
    assert not L.hits_disc(p.box, L.Disc(x, y, 8))


def test_right_edge_is_text_then_circle():
    p = L.place([L.Request("a", "M45 Pleiades", 390, 150, 10)], SIZE, _measure)[0]
    assert p.box[2] <= 390 - 10


def test_first_choice_is_right_of_the_marker():
    p = L.place([L.Request("a", "Mars", 100, 100, 5)], SIZE, _measure)[0]
    assert p.box[0] > 105 and p.box[1] < 100 < p.box[3]


def test_crowd_hides_or_shortens_instead_of_overlapping():
    reqs = [L.Request(i, f"NGC {100 + i} Long Cluster Name", 200 + (i % 3), 150 + (i // 3), 3) for i in range(60)]
    discs = [L.Disc(r.x, r.y, 4, key=r.key) for r in reqs]
    out = L.place(reqs, SIZE, _measure, discs)
    assert L.no_overlaps(out)
    assert any(p.hidden for p in out) and any(p.short for p in out)
    assert all(p.text.startswith("NGC") for p in out)
    for p in out:
        assert (p.box is None) == p.hidden


def test_centred_label_slides_along_its_figure():
    taken = [(150, 140, 250, 160)]                 # something already at the centroid
    p = L.place([L.Request("c", "ORION", 200, 150, mode="centred", slide=[(100, 100), (300, 250)])],
                SIZE, _measure, boxes=taken)[0]
    assert p.box is not None and not L.overlap(p.box, taken[0])


def test_centred_label_near_edge_is_pulled_inside():
    p = L.place([L.Request("c", "CASSIOPEIA", 395, 2, mode="centred")], SIZE, _measure)[0]
    assert L.inside(p.box, *SIZE)


def test_short_forms():
    assert L.short_forms("M45 Pleiades") == ["M45"]
    assert L.short_forms("NGC 869 h Persei Cluster") == ["NGC 869"]
    assert L.short_forms("Mare Tranquillitatis") == ["M. Tranquillitatis"]
    assert L.short_forms("Tycho") == [] and L.short_forms("M34") == []


def test_place_is_deterministic():
    reqs = [L.Request(i, f"Star {i}", 50 + 37 * i % 300, 40 + 53 * i % 220, 4) for i in range(40)]
    a = L.place(copy.deepcopy(reqs), SIZE, _measure)
    b = L.place(copy.deepcopy(reqs), SIZE, _measure)
    assert [(p.key, p.box, p.text) for p in a] == [(p.key, p.box, p.text) for p in b]
    assert L.no_overlaps(a)
