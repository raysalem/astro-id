"""Annotations payload v1 (ANNOTATIONS.md) and the overlay label switch."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import numpy as np
import pytest

import test_synthetic as T
from astro_id import annotations as A
from astro_id import overlay
from test_synthetic import frame_dir, stub_astap  # noqa: F401  (fixtures)

W, H = T.W, T.H


@pytest.fixture(scope="module")
def doc(frame_dir, tmp_path_factory):  # noqa: F811
    from astro_id import cli, solve

    mp = pytest.MonkeyPatch()
    mp.setattr(solve, "solve_with_astap", T.perturbed)
    out = tmp_path_factory.mktemp("ann")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["solve", str(frame_dir), "--offset", "+0m", "--time-tolerance", "15m",
                       "--site", "32.6064,-116.3239,1100", "--overlay-dir", str(out), "--json", "--no-fallback",
                       "--no-overlay"])
    mp.undo()
    assert rc == 0
    return json.loads(buf.getvalue())["results"][0]


def test_payload_required_keys_and_groups(doc):
    a = doc["annotations"]
    assert a["version"] == 1 and a["source"] == "astro-id" and a["title"]
    assert a["image"] == {"width": W, "height": H}
    assert [g["id"] for g in a["groups"]] == ["constellation", "planet", "catalog", "asterism", "star"]
    assert [g["default"] for g in a["groups"]] == [True, True, True, True, False]
    assert a["metrics"] == [{"key": "mag", "label": "Max magnitude", "min": -5, "max": 8, "step": 0.5,
                             "default": 6, "direction": "max"}]
    assert a["score"] == {"label": "Confidence", "min": 0, "max": 100, "default": 65}
    assert "±15 min" in a["legend"] and "ASTAP grade A" in a["legend"]
    assert f"{doc['stars']} stars" in a["legend"] and a["legend"].startswith("2026-11-08 05:15 PST")


def test_items_shapes_bounds_and_references(doc):
    a = doc["annotations"]
    items = a["items"]
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    groups = {g["id"] for g in a["groups"]}
    kinds = set()
    for it in items:
        assert it["group"] in groups and 0 <= it["score"] <= 100 and it["label"]
        kinds.add(it["group"])
        if it["shape"] == "lines":
            assert it["segments"] and len(it["label_at"]) == 2
            assert 0 <= it["label_at"][0] <= W - 1 and 0 <= it["label_at"][1] <= H - 1
            for seg in it["segments"]:
                assert len(seg) == 2 and all(len(p) == 2 for p in seg)
                for x, y in seg:
                    assert -1e-6 <= x <= W - 1 + 1e-6 and -1e-6 <= y <= H - 1 + 1e-6
        else:
            assert it["shape"] in ("point", "circle") and it["r"] > 0
            assert 0 <= it["x"] <= W - 1 and 0 <= it["y"] <= H - 1
        if "member_of" in it:
            assert it["member_of"] in ids and it["member_of"] != it["id"]
    assert {"constellation", "planet", "asterism", "star"} <= kinds
    jup = next(i for i in items if i["id"] == "jupiter")
    assert jup["group"] == "planet" and jup["shape"] == "point" and jup["values"]["mag"] < 0
    reg = next(i for i in items if i["id"] == "regulus")
    assert reg["group"] == "star" and reg["values"]["mag"] == pytest.approx(1.4, abs=0.1)
    assert reg["member_of"] == "sickle-of-leo"
    assert all(i["values"]["mag"] <= A.STAR_MAG_LIMIT for i in items if i["group"] == "star")
    leo = next(i for i in items if i["id"] == "leo")
    assert leo["group"] == "constellation" and len(leo["segments"]) > 5


def test_coordinates_rounded_and_size_capped(doc):
    a = doc["annotations"]
    assert A.encoded_size(a) < A.MAX_BYTES
    for it in a["items"]:
        vals = [it["x"], it["y"]] if "x" in it else [c for s in it["segments"] for p in s for c in p]
        for v in vals:
            assert round(v, 1) == v


def test_unsolved_has_null_annotations(tmp_path, capsys, monkeypatch):
    from PIL import Image

    from astro_id import cli, solve

    monkeypatch.setattr(solve, "solve_with_astap", lambda *a: None)
    Image.fromarray(np.full((200, 300), 30, np.uint8)).save(tmp_path / "b.jpg")
    cli.main(["solve", str(tmp_path / "b.jpg"), "--json", "--no-overlay", "--no-fallback",
              "--overlay-dir", str(tmp_path / "o")])
    assert json.loads(capsys.readouterr().out)["results"][0]["annotations"] is None


def test_lines_follow_distortion_and_clip():
    def project(ra, dec):
        u = (np.asarray(ra) - 100) * 20.0 + 900
        v = (np.asarray(dec) - 10) * 20.0 + 600
        r2 = ((u - 900) ** 2 + (v - 600) ** 2) / 1e6
        return 900 + (u - 900) * (1 - 0.2 * r2), 600 + (v - 600) * (1 - 0.2 * r2)

    segs, verts = A.figure_segments([(90.0, 12.0, 118.0, 8.0)], project, 1800, 1200)
    assert len(segs) > 5
    for (x1, y1), (x2, y2) in segs:
        assert 0 <= x1 <= 1799 and 0 <= x2 <= 1799 and 0 <= y1 <= 1199 and 0 <= y2 <= 1199
    assert A._clip(-100, 50, 100, 50, 80, 80) == (0.0, 50.0, 80.0, 50.0)
    assert A._clip(-10, -10, -5, 500, 80, 80) is None


def test_cap_size_drops_lowest_score_first_keeps_planets():
    items = [{"id": f"s{i}", "label": "x" * 40, "group": "star", "score": 1 + i % 99, "shape": "point",
              "x": 1.0, "y": 1.0, "r": 10, "values": {"mag": 2.0}} for i in range(4000)]
    items.append({"id": "mars", "label": "Mars", "group": "planet", "score": 1, "shape": "point", "x": 1.0,
                  "y": 1.0, "r": 18})
    p = A.cap_size({"items": items})
    ids = {i["id"] for i in p["items"]}
    assert A.encoded_size(p) <= A.MAX_BYTES and "mars" in ids and len(ids) < 4001
    assert A.slug("Orion's Belt") == "orions-belt" and A.slug("M31 Andromeda Galaxy") == "m31-andromeda-galaxy"


def _labels(monkeypatch, frame_dir, tmp_path, *extra):
    from astro_id import cli, solve

    monkeypatch.setattr(solve, "solve_with_astap", T.perturbed)
    seen: list[str] = []
    orig = overlay._label

    def rec(d, xy, text, *a, **k):
        seen.append(text)
        return orig(d, xy, text, *a, **k)

    monkeypatch.setattr(overlay, "_label", rec)
    rc = cli.main(["solve", str(frame_dir), "--offset", "+0m", "--site", "32.6064,-116.3239,1100",
                   "--overlay-dir", str(tmp_path), "--no-fallback", *extra])
    assert rc == 0
    return seen


def test_overlay_labels_are_plain_names_by_default(frame_dir, tmp_path, monkeypatch):  # noqa: F811
    seen = _labels(monkeypatch, frame_dir, tmp_path)
    assert "Jupiter" in seen and "LEO" in seen
    assert not any(t.rsplit(" ", 1)[-1].isdigit() and " " in t for t in seen)


def test_overlay_scores_flag_adds_scores(frame_dir, tmp_path, monkeypatch):  # noqa: F811
    seen = _labels(monkeypatch, frame_dir, tmp_path, "--overlay-scores")
    assert any(t.startswith("Jupiter ") and t.rsplit(" ", 1)[1].isdigit() for t in seen)
    assert any(t.startswith("LEO ") for t in seen)
