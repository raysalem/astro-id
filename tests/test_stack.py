"""Stacking sequences: grouping, registration, representative solve + stack-copy."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from astro_id import cli, solve
from astro_id.imageio import ExifInfo
from astro_id.stack import _accept, group_frames, register, representative

import test_synthetic as T


def ex(t, exp=10.0, focal=20.0, lens="FE 20mm F1.8 G", w=9504, h=6336):
    return ExifInfo(t, focal, None, w, h, exp, lens)


T0 = datetime(2019, 11, 5, 9, 26)


def test_group_frames_by_gap_and_setup():
    files = [Path(f"F{i}.JPG") for i in range(8)]
    exifs = {
        files[0]: ex(T0), files[1]: ex(T0 + timedelta(seconds=35)), files[2]: ex(T0 + timedelta(seconds=150)),
        files[3]: ex(T0 + timedelta(seconds=300)),                       # gap 150 s > 120: new group
        files[4]: ex(T0 + timedelta(seconds=330), exp=13.0),             # exposure changed
        files[5]: ex(T0 + timedelta(seconds=360), exp=13.0, lens="Batis 2/135"),  # lens changed
        files[6]: None,                                                  # no EXIF
        files[7]: ex(None),                                              # no time
    }
    g = group_frames(files, exifs, 120)
    assert g == [[files[0], files[1], files[2]], [files[3]], [files[4]], [files[5]], [files[6]], [files[7]]]
    assert representative(g[0]) == files[1]
    assert representative([files[0], files[1]]) == files[1]
    assert group_frames(files[:3], exifs, 40) == [[files[0], files[1]], [files[2]]]


def test_group_frames_sorts_by_time_not_name():
    a, b, c = Path("A.JPG"), Path("B.JPG"), Path("C.JPG")
    exifs = {a: ex(T0 + timedelta(seconds=60)), b: ex(T0), c: ex(T0 + timedelta(seconds=999))}
    assert group_frames([a, b, c], exifs, 120) == [[b, a], [c]]


def _field(n=400, seed=3, w=3000, h=2000):
    rng = np.random.default_rng(seed)
    return np.c_[rng.uniform(0, w, n), rng.uniform(0, h, n)]


def _move(xy, dx, dy, rot_deg=0.0, c=(1500, 1000)):
    a = math.radians(rot_deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return (xy - c) @ R.T + c + [dx, dy]


def test_register_recovers_shift_and_rotation():
    rep = _field()
    other = _move(rep, 14.0, -9.0, 0.4)
    m = register(rep, rep[:150], other, other[:150], 3000, 2000, 3.0, max_shift_px=72)
    assert m is not None and m.pairs > 300 and m.bright_fraction > 0.9
    assert m.offset_px == pytest.approx(math.hypot(14, -9), abs=0.5)
    assert m.rotation_deg == pytest.approx(0.4, abs=0.02)
    x, y = m.apply([100.0], [200.0])
    tx, ty = _move(np.array([[100.0, 200.0]]), 14.0, -9.0, 0.4)[0]
    assert math.hypot(x[0] - tx, y[0] - ty) < 0.1
    ix, iy = m.inverse(x, y)
    assert ix[0] == pytest.approx(100.0, abs=1e-6) and iy[0] == pytest.approx(200.0, abs=1e-6)
    assert _accept(m, 72)


def test_register_rejects_other_field_and_big_shift():
    rep = _field(seed=3)
    assert register(rep, rep[:150], _field(seed=9), _field(seed=9)[:150], 3000, 2000, 3.0, 72) is None
    far = _move(rep, 300.0, 0.0)
    m = register(rep, rep[:150], far, far[:150], 3000, 2000, 3.0, 72)
    assert m is None or not _accept(m, 72)


# ---- end to end on synthetic frames: the middle frame is solved, its neighbours reuse it ----

@pytest.fixture(scope="module")
def seq_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("seq")
    T.render(d / "SEQ0001.JPG", "2026:11:08 05:15:00", (0.0, 0.0), 10.0)
    T.render(d / "SEQ0002.JPG", "2026:11:08 05:15:40", (3.0, -2.0), 10.0)
    T.render(d / "SEQ0003.JPG", "2026:11:08 05:16:20", (6.0, -4.0), 10.0)
    T.render(d / "SEQ0004.JPG", "2026:11:08 05:17:00", (5.0, -3.0), 13.0)  # other exposure: alone
    return d


def _solve(capsys, d, out, *extra):
    rc = cli.main(["solve", str(d), "--offset", "+0m", "--site", "32.6064,-116.3239,1100", "--overlay-dir", str(out),
                   "--json", "--no-fallback", *extra])
    assert rc == 0
    return {Path(r["file"]).name: r for r in json.loads(capsys.readouterr().out)["results"]}


def test_sequence_representative_and_copies(seq_dir, monkeypatch, capsys, tmp_path):
    calls = []

    def astap(*a):
        calls.append(1)
        return T.perturbed(*a)

    monkeypatch.setattr(solve, "solve_with_astap", astap)
    r = _solve(capsys, seq_dir, tmp_path / "o")
    T._check_contract({"contract": 1, "results": list(r.values())})
    rep = r["SEQ0002.JPG"]
    assert rep["solver"] == "astap" and rep["stack_of"] is None
    for n in ("SEQ0001.JPG", "SEQ0003.JPG"):
        assert r[n]["solver"] == "stack-copy" and r[n]["stack_of"] == rep["file"] and r[n]["solved"]
        assert {o["tag"] for o in r[n]["objects"] if o["taggable"] and o["confidence"] >= 65} >= {"jupiter", "leo"}
        assert r[n]["overlay"] and Path(r[n]["overlay"]).exists()
        assert r[n]["true_time"] != rep["true_time"]  # each frame keeps its own time
    assert r["SEQ0004.JPG"]["solver"] == "astap"
    assert len(calls) == 2  # representative + the different-exposure frame
    # positions are mapped into each frame: Jupiter in frame 1 sits 3,-2 px from frame 2
    j1 = next(o for o in r["SEQ0001.JPG"]["objects"] if o["tag"] == "jupiter")
    j2 = next(o for o in rep["objects"] if o["tag"] == "jupiter")
    assert math.hypot(j2["x"] - j1["x"] - 3.0, j2["y"] - j1["y"] + 2.0) < 1.0
    csv = (tmp_path / "o" / "astro-id-report.csv").read_text(encoding="utf-8")
    header = csv.splitlines()[0].split(",")
    for col in ("level", "magnitude", "parent", "member_of", "taggable", "stack_of"):
        assert col in header
    assert "SEQ0002.JPG" in csv.split("SEQ0001.JPG", 1)[1].splitlines()[0]  # stack_of column filled


def test_stack_mode_all_solves_every_frame(seq_dir, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(solve, "solve_with_astap", T.perturbed)
    r = _solve(capsys, seq_dir, tmp_path / "o", "--stack-mode", "all", "--no-overlay")
    assert all(x["solver"] == "astap" and x["stack_of"] is None for x in r.values())


def test_repointed_frame_in_sequence_is_solved_alone(tmp_path, monkeypatch, capsys):
    d = tmp_path / "rp"
    d.mkdir()
    T.render(d / "RP0001.JPG", "2026:11:08 05:15:00", (0.0, 0.0), 10.0)
    T.render(d / "RP0002.JPG", "2026:11:08 05:15:40", (0.0, 0.0), 10.0)
    T.render(d / "RP0003.JPG", "2026:11:08 05:16:20", (400.0, 150.0), 10.0)  # re-pointed by ~9 deg
    monkeypatch.setattr(solve, "solve_with_astap", T.perturbed)
    r = _solve(capsys, d, tmp_path / "o", "--no-overlay")
    assert r["RP0001.JPG"]["solver"] == "stack-copy"
    assert r["RP0003.JPG"]["solver"] != "stack-copy" and r["RP0003.JPG"]["stack_of"] is None
