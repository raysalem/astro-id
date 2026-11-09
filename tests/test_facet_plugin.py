r"""Tests for facet_plugin/astro_id.py against a real Facet schema.

Run with Facet's venv python (needs the Facet checkout importable):
  C:\Users\raysa\AppData\Local\Facet\venv\Scripts\python.exe -m pytest tests/test_facet_plugin.py
Skips cleanly when Facet is not importable (e.g. the engine's `uv run pytest`).
"""
import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FACET = Path(os.environ.get("FACET_DIR", r"C:\Users\raysa\facet-wintest"))
STUB = HERE / "fixtures" / "contract_stub.json"
PLUGIN = HERE.parent / "facet_plugin" / "astro_id.py"

if (FACET / "db" / "manual_tags.py").exists() and str(FACET) not in sys.path:
    sys.path.append(str(FACET))
try:
    from db.schema import init_database
    from db.manual_tags import insert_manual_tag, MAX_TAGS_PER_PHOTO
except Exception as ex:  # pragma: no cover
    pytest.skip(f"Facet not importable: {ex}", allow_module_level=True)

spec = importlib.util.spec_from_file_location("astro_id_plugin", PLUGIN)
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

P1, P2, P3 = ("C:/photos/night/IMG_0001.JPG", "C:/photos/night/IMG_0002.JPG",
              "C:/photos/night/IMG_0003.JPG")


def stub():
    return json.loads(STUB.read_text(encoding="utf-8"))


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_database(path)
    conn = sqlite3.connect(path)
    for p in (P1, P2, P3, "C:/photos/night/RAW_0009.ARW"):
        conn.execute("INSERT INTO photos(path, filename) VALUES (?, ?)", (p, os.path.basename(p)))
    conn.commit()
    conn.close()
    ap.configure({"engine": "X", "settle_s": 0, "batch_min": 99}, path)
    yield path
    ap.configure(None, None)


@pytest.fixture
def engine(monkeypatch):
    class Calls(list):
        extras = None
    calls = Calls()

    def fake(target, mc, cfg, extra=None):
        calls.append((target, mc))
        fake.extras.append(extra)
        return ap.parse_engine_output(json.dumps(stub()))
    fake.extras = []
    calls.extras = fake.extras
    monkeypatch.setattr(ap, "_run_engine", fake)
    return calls


def tags(db, p, owner=None):
    c = sqlite3.connect(db)
    q = "SELECT tag, source, created_by FROM photo_manual_tags WHERE photo_path=?"
    rows = c.execute(q, (p,)).fetchall()
    c.close()
    return rows


def astro_tags(db, p):
    return sorted(t for t, _, cb in tags(db, p) if cb == "astro-id")


def run(db, paths, mc=65):
    return ap.process_paths(paths, ap.get_config(), db, mc)


def test_threshold_65(db, engine):
    run(db, [P1])
    assert astro_tags(db, P1) == sorted(["plate-solved", "cygnus", "deneb", "m31 andromeda galaxy"])
    assert "m33 triangulum galaxy" not in astro_tags(db, P1)


def test_rerun_at_80_removes_lower_and_is_idempotent(db, engine):
    run(db, [P1], 65)
    run(db, [P1], 80)
    assert astro_tags(db, P1) == sorted(["plate-solved", "cygnus", "deneb"])
    before = tags(db, P1)
    run(db, [P1], 80)
    assert sorted(tags(db, P1)) == sorted(before)


def test_other_source_tags_untouched(db, engine):
    c = sqlite3.connect(db)
    insert_manual_tag(c, P1, "family trip", "user", None)
    insert_manual_tag(c, P1, "from xmp", "xmp", None)
    insert_manual_tag(c, P1, "deneb", "user", None)   # user already owns this one
    c.commit()
    c.close()
    run(db, [P1], 65)
    run(db, [P1], 90)
    rows = {t: (s, cb) for t, s, cb in tags(db, P1)}
    assert rows["family trip"] == ("user", None)
    assert rows["from xmp"] == ("xmp", None)
    assert rows["deneb"] == ("user", None)            # never taken over or deleted
    assert "m31 andromeda galaxy" not in rows


def test_unsolved_gets_failed_tag(db, engine):
    run(db, [P3])
    assert astro_tags(db, P3) == ["plate-solve failed"]


def test_non_astro_ignored(db, monkeypatch):
    seen = []
    monkeypatch.setattr(ap._worker, "submit", lambda p: seen.append(p))
    ap.on_score_complete({"path": P1, "category": "landscape", "tags": "tree, sky"})
    assert seen == []
    ap.on_score_complete({"path": P1, "category": "astro", "tags": ""})
    ap.on_score_complete({"path": P2, "category": "x", "tags": "Milky Way, tree"})
    assert seen == [P1, P2]
    ap.configure({"engine": "X", "roots": ["C:/photos/night"]}, db)
    ap.on_score_complete({"path": P3, "category": "x", "tags": ""})
    assert seen[-1] == P3
    ap.on_score_complete({"path": "D:/other/a.jpg", "category": "x", "tags": ""})
    assert len(seen) == 3


def test_disabled_or_no_engine_skips(db, monkeypatch):
    seen = []
    monkeypatch.setattr(ap._worker, "submit", lambda p: seen.append(p))
    ap.configure({"engine": "X", "enabled": False}, db)
    ap.on_score_complete({"path": P1, "category": "astro"})
    ap.configure({"engine": None}, db)
    ap.on_score_complete({"path": P1, "category": "astro"})
    assert seen == []


def test_contract_mismatch_rejected(db):
    d = stub()
    d["contract"] = 2
    with pytest.raises(ap.EngineError):
        ap.parse_engine_output(json.dumps(d))
    with pytest.raises(ap.EngineError):
        ap.parse_engine_output("not json")


def test_contract_mismatch_writes_nothing(db, monkeypatch):
    def bad(target, mc, cfg, extra=None):
        d = stub(); d["contract"] = 9
        return ap.parse_engine_output(json.dumps(d))
    monkeypatch.setattr(ap, "_run_engine", bad)
    assert run(db, [P1]) == {}
    assert tags(db, P1) == []


def test_raw_jpg_pair_mapping(db, engine):
    raw = "C:/photos/night/IMG_0001.ARW"
    c = sqlite3.connect(db)
    c.execute("INSERT INTO photos(path, filename) VALUES (?, ?)", (raw, "IMG_0001.ARW"))
    c.commit(); c.close()
    run(db, [raw])
    assert "cygnus" in astro_tags(db, raw)          # tags land on the event (RAW) path
    assert tags(db, P1) == []                        # JPG row untouched when not requested


def test_path_form_mapping(db):
    m = ap.map_results([r"C:\photos\night\IMG_0001.JPG"], stub()["results"])
    assert len(m) == 1


def test_tag_cap(db, engine, monkeypatch):
    c = sqlite3.connect(db)
    for i in range(MAX_TAGS_PER_PHOTO - 3):
        insert_manual_tag(c, P1, f"other {i}", "user", None)
    c.commit(); c.close()
    run(db, [P1])
    mine = astro_tags(db, P1)
    assert len(mine) == 3
    assert "plate-solved" in mine and "cygnus" in mine and "deneb" in mine   # highest first
    assert len(tags(db, P1)) == MAX_TAGS_PER_PHOTO


def test_dry_run_writes_nothing(db, engine):
    ch = ap.process_paths([P1], ap.get_config(), db, 65, dry_run=True)
    assert "cygnus" in ch[P1][0]
    assert tags(db, P1) == []


def test_missing_photo_skipped(db, engine):
    assert run(db, ["C:/photos/night/IMG_0001.png"]) == {}


def test_folder_batch_single_engine_call(db, engine):
    ap.configure({"engine": "X", "batch_min": 2}, db)
    run(db, [P1, P2])
    assert engine == [("C:/photos/night", 65)]
    assert "jupiter" in astro_tags(db, P2)


def test_worker_end_to_end(db, engine):
    assert ap._worker.submit(P1) is True
    ap._worker.submit(P1)  # dedup is best effort once the job is running
    for _ in range(50):
        if astro_tags(db, P1):
            break
        time.sleep(0.1)
    assert "cygnus" in astro_tags(db, P1)


# ---- Facet as source of truth for location and time ----

def set_photo(db, path, lat=None, lon=None, taken=None, manual=0):
    c = sqlite3.connect(db)
    c.execute("UPDATE photos SET gps_latitude=?, gps_longitude=?, date_taken=?, date_manually_set=? WHERE path=?",
              (lat, lon, taken, manual, path))
    c.commit(); c.close()


def flags(engine):
    return engine.extras[-1]


def test_manual_date_wins_and_is_passed_as_time(db, engine):
    set_photo(db, P1, taken="2026:11:07 20:34:00", manual=1)
    run(db, [P1])
    f = flags(engine)
    assert f[f.index("--time") + 1] == "2026-11-07T20:34:00"
    assert "--site" not in f                      # no GPS, no json from the plugin: engine decides


def test_non_manual_date_is_never_passed(db, engine):
    set_photo(db, P1, taken="2026:11:07 20:34:00", manual=0)   # camera clock = wrong; json offset path
    run(db, [P1])
    assert "--time" not in flags(engine)


def test_facet_gps_passed_as_site_manual_or_exif(db, engine):
    set_photo(db, P1, lat=32.6064, lon=-116.3239)               # EXIF GPS also counts
    run(db, [P1])
    f = flags(engine)
    assert f[f.index("--site") + 1] == "32.606400,-116.323900"


def test_no_gps_no_manual_date_runs_bare(db, engine):
    run(db, [P1])
    assert flags(engine) == []


def test_zero_zero_gps_ignored(db, engine):
    set_photo(db, P1, lat=0.0, lon=0.0)
    run(db, [P1])
    assert "--site" not in flags(engine)


def test_timezone_and_tolerance_from_plugin_config(db, engine):
    set_photo(db, P1, taken="2026:11:07 20:34:00", manual=1)
    ap.configure({"engine": "X", "batch_min": 99, "timezone": "America/Los_Angeles",
                  "time_tolerance_min": 10}, db)
    run(db, [P1])
    f = flags(engine)
    assert f[f.index("--timezone") + 1] == "America/Los_Angeles"
    assert f[f.index("--time-tolerance") + 1] == "10"


def test_batching_only_groups_same_site_without_own_time(db, engine):
    ap.configure({"engine": "X", "batch_min": 2}, db)
    set_photo(db, P1, lat=1.0, lon=2.0)
    set_photo(db, P2, lat=1.0, lon=2.0)
    set_photo(db, P3, lat=5.0, lon=6.0)
    run(db, [P1, P2, P3])
    targets = [t for t, _ in engine]
    assert targets.count("C:/photos/night") == 1 and P3 in targets      # P1+P2 batch, P3 alone
    assert "--site" in engine.extras[0] and engine.extras[0][engine.extras[0].index("--site") + 1] == "1.000000,2.000000"


def test_photo_with_manual_time_never_in_folder_run(db, engine):
    ap.configure({"engine": "X", "batch_min": 2}, db)
    set_photo(db, P2, taken="2026:11:08 05:11:00", manual=1)
    run(db, [P1, P2, P3])
    targets = [t for t, _ in engine]
    assert P2 in targets and "C:/photos/night" in targets
    i = targets.index(P2)
    assert "--time" in engine.extras[i]
