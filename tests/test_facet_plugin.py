r"""Tests for facet_plugin/astro_id.py against a real Facet schema.

Run with Facet's venv python (needs the Facet checkout importable):
  %LOCALAPPDATA%\Facet\venv\Scripts\python.exe -m pytest tests/test_facet_plugin.py
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
FACET = Path(os.environ.get("FACET_DIR", str(Path.home() / "facet-wintest")))
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
    ap.configure({"engine": "X", "trigger": "scan", "settle_s": 0, "batch_min": 99}, path)
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
    ap.configure({"engine": "X", "trigger": "scan", "roots": ["C:/photos/night"]}, db)
    ap.on_score_complete({"path": P3, "category": "x", "tags": ""})
    assert seen[-1] == P3
    ap.on_score_complete({"path": "D:/other/a.jpg", "category": "x", "tags": ""})
    assert len(seen) == 3


def test_disabled_or_no_engine_skips(db, monkeypatch):
    seen = []
    monkeypatch.setattr(ap._worker, "submit", lambda p: seen.append(p))
    ap.configure({"engine": "X", "trigger": "scan", "enabled": False}, db)
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
    # cap order: status, then level (constellation 1, catalog 3) before stars (5)
    assert "plate-solved" in mine and "cygnus" in mine and "m31 andromeda galaxy" in mine
    assert len(tags(db, P1)) == MAX_TAGS_PER_PHOTO


def test_dry_run_writes_nothing(db, engine):
    ch = ap.process_paths([P1], ap.get_config(), db, 65, dry_run=True)
    assert "cygnus" in ch[P1][0]
    assert tags(db, P1) == []


def test_missing_photo_skipped(db, engine):
    assert run(db, ["C:/photos/night/IMG_0001.png"]) == {}


def test_folder_batch_single_engine_call(db, engine):
    ap.configure({"engine": "X", "trigger": "scan", "batch_min": 2}, db)
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
    ap.configure({"engine": "X", "trigger": "scan", "batch_min": 99, "timezone": "America/Los_Angeles",
                  "time_tolerance_min": 10}, db)
    run(db, [P1])
    f = flags(engine)
    assert f[f.index("--timezone") + 1] == "America/Los_Angeles"
    assert f[f.index("--time-tolerance") + 1] == "10"


def test_batching_only_groups_same_site_without_own_time(db, engine):
    ap.configure({"engine": "X", "trigger": "scan", "batch_min": 2}, db)
    set_photo(db, P1, lat=1.0, lon=2.0)
    set_photo(db, P2, lat=1.0, lon=2.0)
    set_photo(db, P3, lat=5.0, lon=6.0)
    run(db, [P1, P2, P3])
    targets = [t for t, _ in engine]
    assert targets.count("C:/photos/night") == 1 and P3 in targets      # P1+P2 batch, P3 alone
    assert "--site" in engine.extras[0] and engine.extras[0][engine.extras[0].index("--site") + 1] == "1.000000,2.000000"


def test_photo_with_manual_time_never_in_folder_run(db, engine):
    ap.configure({"engine": "X", "trigger": "scan", "batch_min": 2}, db)
    set_photo(db, P2, taken="2026:11:08 05:11:00", manual=1)
    run(db, [P1, P2, P3])
    targets = [t for t, _ in engine]
    assert P2 in targets and "C:/photos/night" in targets
    i = targets.index(P2)
    assert "--time" in engine.extras[i]


# ---- tag hierarchy (contract v1 additive: level / taggable) ----

def _obj(tag, level, conf=100, taggable=True, kind="star"):
    return {"name": tag, "tag": tag, "kind": kind, "confidence": conf, "tier": "high", "x": 1.0, "y": 1.0,
            "level": level, "magnitude": 1.0, "parent": None, "member_of": None, "taggable": taggable}


def test_desired_tags_filters_taggable_and_orders_by_level():
    r = {"solved": True, "objects": [
        _obj("deneb", 5, 99), _obj("orion's belt", 4, 70, kind="asterism"), _obj("alnitak", 5, 100, taggable=False),
        _obj("m45 pleiades", 3, 90, kind="cluster"), _obj("uranus", 2, 80, kind="planet"),
        _obj("orion", 1, 66, kind="constellation"), _obj("taurus", 1, 95, kind="constellation"),
        _obj("faint", 2, 50, kind="planet")]}
    assert ap.desired_tags(r, 65) == ["plate-solved", "taurus", "orion", "uranus", "m45 pleiades",
                                      "orion's belt", "deneb"]


def test_old_engine_without_hierarchy_fields_still_works():
    r = {"solved": True, "objects": [{"tag": "cygnus", "confidence": 90}, {"tag": "deneb", "confidence": 95}]}
    assert ap.desired_tags(r, 65) == ["plate-solved", "deneb", "cygnus"]


def test_cap_keeps_planets_uranus_regression(db, monkeypatch):
    """60 taggable objects at confidence 100 (stars on): Uranus must survive the 50-tag cap."""
    objs = [_obj(f"star {i:02d}", 5, 100) for i in range(52)]
    objs += [_obj(f"const {i}", 1, 90, kind="constellation") for i in range(6)]
    objs += [_obj("uranus", 2, 100, kind="planet"), _obj("jupiter", 2, 100, kind="planet")]
    frame = {"file": P1, "solved": True, "objects": objs}
    monkeypatch.setattr(ap, "_run_engine", lambda *a, **k: [frame])
    run(db, [P1])
    mine = astro_tags(db, P1)
    assert len(mine) == MAX_TAGS_PER_PHOTO
    assert "uranus" in mine and "jupiter" in mine and "plate-solved" in mine
    assert all(f"const {i}" in mine for i in range(6))
    assert sum(t.startswith("star ") for t in mine) == MAX_TAGS_PER_PHOTO - 9


def test_kind_style_tags_with_slash_are_valid(db, monkeypatch):
    frame = {"file": P1, "solved": True, "objects": [
        _obj("constellation/orion", 1, kind="constellation"), _obj("planet/jupiter", 2, kind="planet"),
        _obj("messier/m45 pleiades", 3, kind="cluster"), _obj("asterism/orion's belt", 4, kind="asterism")]}
    monkeypatch.setattr(ap, "_run_engine", lambda *a, **k: [frame])
    run(db, [P1])
    assert astro_tags(db, P1) == sorted(["plate-solved", "constellation/orion", "planet/jupiter",
                                         "messier/m45 pleiades", "asterism/orion's belt"])


def test_policy_and_stack_flags_passed_to_engine(db, engine):
    ap.configure({"engine": "X", "trigger": "scan", "batch_min": 99, "tag_levels": [1, 2, 3, 4, 5], "star_mag_limit": 1.5,
                  "max_catalog_mag": 5.0, "tag_style": "kind", "stack_mode": "all", "stack_gap_s": 60}, db)
    run(db, [P1])
    f = flags(engine)
    assert f[f.index("--tag-levels") + 1] == "1,2,3,4,5"
    assert f[f.index("--star-mag-limit") + 1] == "1.5"
    assert f[f.index("--max-catalog-mag") + 1] == "5.0"
    assert f[f.index("--tag-style") + 1] == "kind"
    assert f[f.index("--stack-mode") + 1] == "all"
    assert f[f.index("--stack-gap") + 1] == "60"


def test_unset_policy_keys_pass_nothing(db, engine):
    run(db, [P1])
    assert not any(x.startswith(("--tag", "--star", "--max-catalog", "--stack")) for x in flags(engine))


# ---- pending selection, lock, exit hook ----

ROOT = "C:/photos/night"
OUTSIDE = "D:/misc/astro_0001.JPG"
RAW9 = "C:/photos/night/RAW_0009.ARW"


def _setup_pending(db):
    c = sqlite3.connect(db)
    c.execute("UPDATE photos SET scanned_at='2026-11-09 18:00:00'")
    c.execute("INSERT INTO photos(path, filename, category, scanned_at) VALUES (?,?,?,?)",
              (OUTSIDE, "astro_0001.JPG", "astro", "2026-11-09 18:00:00"))
    c.execute("INSERT INTO photos(path, filename, category, tags, scanned_at) VALUES (?,?,?,?,?)",
              ("D:/misc/dog.JPG", "dog.JPG", "pets", "dog,grass", "2026-11-10 09:00:00"))
    # P1: tagged after its scan -> done. P2: rescanned after its tags -> pending. P3 + RAW: no tags -> pending.
    insert_manual_tag(c, P1, "plate-solved", "user", "astro-id", "2026-11-09 18:30:00")
    insert_manual_tag(c, P2, "plate-solved", "user", "astro-id", "2026-11-09 18:30:00")
    insert_manual_tag(c, P1, "family", "user", None, "2026-11-01 00:00:00")
    c.execute("UPDATE photos SET scanned_at='2026-11-10 08:00:00' WHERE path=?", (P2,))
    c.commit(); c.close()


def test_pending_selection(db):
    _setup_pending(db)
    cfg = {"roots": [ROOT]}
    assert ap.select_pending(db, cfg) == sorted([P2, P3, RAW9, OUTSIDE])        # all astro, no since
    got = ap.select_pending(db, cfg, since="2026-11-09 19:00:00")
    assert OUTSIDE not in got and P3 in got and P2 in got                        # roots ignore since
    c = sqlite3.connect(db)
    c.execute("UPDATE photos SET scanned_at='2026-11-10 09:00:00' WHERE path=?", (OUTSIDE,))
    c.commit(); c.close()
    assert OUTSIDE in ap.select_pending(db, cfg, since="2026-11-09 19:00:00")  # scanned in this run
    assert "D:/misc/dog.JPG" not in ap.select_pending(db, cfg)                 # never astro


def test_lock_exclusive_and_stale(tmp_path, monkeypatch):
    lock = str(tmp_path / "logs" / "x.lock")
    assert ap.acquire_lock(lock)
    monkeypatch.setattr(ap, "_pid_alive", lambda pid: True)
    Path(lock).write_text("999999 t", encoding="utf-8")
    assert ap.lock_held(lock) and not ap.acquire_lock(lock)                     # live owner
    monkeypatch.setattr(ap, "_pid_alive", lambda pid: False)                     # dead owner: stale
    assert not ap.lock_held(lock) and ap.acquire_lock(lock)
    ap.release_lock(lock)
    assert not Path(lock).exists()


def test_pid_alive_real_process():
    assert ap._pid_alive(os.getpid())
    assert not ap._pid_alive(0)


def test_pending_cli_tags_and_releases_lock(db, engine, tmp_path):
    _setup_pending(db)
    log = tmp_path / "logs" / "astro-id.log"
    ap.configure({"engine": "X", "trigger": "scan", "batch_min": 99, "roots": [ROOT], "log_path": str(log)}, db)
    assert ap.main(["--pending", "--db", db]) == 0
    assert "plate-solve failed" in astro_tags(db, P3)          # stub frame 3 is unsolved
    assert "jupiter" in astro_tags(db, P2)                     # rescanned photo re-tagged
    assert astro_tags(db, P1) == ["plate-solved"]              # up to date: not re-run
    assert sorted(t for t, _ in engine) == sorted([P2, P3, RAW9, OUTSIDE])
    assert not (tmp_path / "logs" / "astro-id-catchup.lock").exists()
    assert ("family", "user", None) in tags(db, P1)            # other sources untouched


def test_pending_skips_when_lock_held(db, engine, tmp_path, monkeypatch):
    _setup_pending(db)
    log = tmp_path / "logs" / "astro-id.log"
    ap.configure({"engine": "X", "trigger": "scan", "roots": [ROOT], "log_path": str(log)}, db)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "astro-id-catchup.lock").write_text("424242 t", encoding="utf-8")
    monkeypatch.setattr(ap, "_pid_alive", lambda pid: True)
    assert ap.run_pending(ap.get_config(), db) == {}
    assert engine == []


class _FakePopen:
    calls = []

    def __init__(self, cmd, **kw):
        _FakePopen.calls.append((cmd, kw))
        self.pid = 4321


def test_exit_hook_spawns_detached_catch_up(db, tmp_path, monkeypatch):
    _setup_pending(db)
    log = tmp_path / "logs" / "astro-id.log"
    ap.configure({"engine": "X", "trigger": "scan", "roots": [ROOT], "log_path": str(log)}, db)
    _FakePopen.calls = []
    monkeypatch.setattr(ap.subprocess, "Popen", _FakePopen)
    ap._on_exit()
    assert len(_FakePopen.calls) == 1
    cmd, kw = _FakePopen.calls[0]
    assert cmd[0] == sys.executable and cmd[1].endswith("astro_id.py")
    assert "--pending" in cmd and cmd[cmd.index("--since") + 1] == ap._PROCESS_START
    assert kw["env"]["DB_PATH"] == os.path.abspath(db)
    if os.name == "nt":
        assert kw["creationflags"] & 0x08000000 and kw["creationflags"] & 0x00000008
    assert kw["stdin"] == ap.subprocess.DEVNULL and log.exists()


def test_exit_hook_no_pending_or_lock_or_disabled_spawns_nothing(db, tmp_path, monkeypatch):
    log = tmp_path / "logs" / "astro-id.log"
    _FakePopen.calls = []
    monkeypatch.setattr(ap.subprocess, "Popen", _FakePopen)
    c = sqlite3.connect(db)
    for p in (P1, P2, P3, RAW9):
        insert_manual_tag(c, p, "plate-solved", "user", "astro-id")
    c.commit(); c.close()
    ap.configure({"engine": "X", "trigger": "scan", "roots": [ROOT], "log_path": str(log)}, db)
    ap._on_exit()                                               # everything tagged
    _setup_pending(db)
    ap.configure({"engine": "X", "trigger": "scan", "roots": [ROOT], "log_path": str(log), "catch_up": False}, db)
    ap._on_exit()                                               # disabled
    ap.configure({"engine": "X", "trigger": "scan", "roots": [ROOT], "log_path": str(log)}, db)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "astro-id-catchup.lock").write_text("424242 t", encoding="utf-8")
    monkeypatch.setattr(ap, "_pid_alive", lambda pid: True)
    ap._on_exit()                                               # a catch-up is running
    assert _FakePopen.calls == []


def _load_plugin_with_argv(monkeypatch, argv0):
    registered = []
    monkeypatch.setattr(sys, "argv", [argv0, "x"])
    import atexit
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))
    sp = importlib.util.spec_from_file_location("astro_id_argv_probe", PLUGIN)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod, registered


def test_exit_hook_registers_only_in_scan_process(monkeypatch):
    mod, reg = _load_plugin_with_argv(monkeypatch, r"C:\Users\x\facet-wintest\facet.py")
    assert reg == [mod._on_exit] and mod._exit_hook_registered
    for argv0 in ("viewer.py", r"C:\venv\Scripts\uvicorn.exe", "-c", "pytest"):
        mod, reg = _load_plugin_with_argv(monkeypatch, argv0)
        assert reg == [] and not mod._exit_hook_registered
