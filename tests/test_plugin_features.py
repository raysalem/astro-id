"""Annotation storage, config validation, solve_source flag, RAW XMP sidecar, RAW source choice."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

TF = pytest.importorskip("test_facet_plugin")
ap = TF.ap
db, engine = TF.db, TF.engine  # fixtures
P1, P2, P3 = TF.P1, TF.P2, TF.P3

TABLE = ("CREATE TABLE photo_annotations (photo_path TEXT, source TEXT, payload TEXT, created_at TEXT, "
         "PRIMARY KEY (photo_path, source))")


def _rows(path):
    c = sqlite3.connect(path)
    r = {(p, s): json.loads(pl) for p, s, pl in c.execute("SELECT photo_path, source, payload FROM photo_annotations")}
    c.close()
    return r


@pytest.fixture
def helper(monkeypatch):
    """A fake Facet db.annotations helper (the real one lives on Facet's feat/photo-annotations)."""
    mod = types.ModuleType("db.annotations")

    def upsert_annotation(conn, photo_path, source, payload):
        conn.execute("INSERT OR REPLACE INTO photo_annotations VALUES (?, ?, ?, datetime('now'))",
                     (photo_path, source, json.dumps(payload)))
    mod.upsert_annotation = upsert_annotation
    monkeypatch.setitem(sys.modules, "db.annotations", mod)
    monkeypatch.setattr(ap, "_annotation_skip_logged", False)
    return mod


def test_annotation_upserted_with_tags_and_replaced_on_rerun(db, engine, helper):
    c = sqlite3.connect(db)
    c.execute(TABLE)
    c.commit()
    c.close()
    TF.run(db, [P1, P3])
    rows = _rows(db)
    assert rows[(P1, "astro-id")]["items"][0]["id"] == "deneb"
    assert (P3, "astro-id") not in rows                       # unsolved: nothing to store
    assert "cygnus" in TF.astro_tags(db, P1)
    c = sqlite3.connect(db)                                    # old row, then a failed re-solve deletes it
    c.execute("INSERT OR REPLACE INTO photo_annotations VALUES (?, 'astro-id', '{}', 'x')", (P3,))
    c.commit()
    c.close()
    TF.run(db, [P3])
    assert (P3, "astro-id") not in _rows(db)
    assert TF.astro_tags(db, P3) == ["plate-solve failed"]
    TF.run(db, [P1])                                           # re-run replaces, never duplicates
    assert len([k for k in _rows(db) if k[0] == P1]) == 1


def test_annotation_left_alone_when_engine_sends_none(db, engine, helper):
    c = sqlite3.connect(db)
    c.execute(TABLE)
    c.execute("INSERT INTO photo_annotations VALUES (?, 'astro-id', '{\"version\": 1}', 'x')", (P2,))
    c.commit()
    c.close()
    TF.run(db, [P2])                                           # stub frame 2 is solved but carries no payload
    assert _rows(db)[(P2, "astro-id")] == {"version": 1}


def test_annotation_skipped_gracefully_without_helper(db, engine, monkeypatch):
    monkeypatch.setitem(sys.modules, "db.annotations", None)   # import fails
    monkeypatch.setattr(ap, "_annotation_skip_logged", False)
    c = sqlite3.connect(db)
    c.execute(TABLE)
    c.commit()
    c.close()
    TF.run(db, [P1])
    assert "cygnus" in TF.astro_tags(db, P1) and _rows(db) == {}


def test_annotation_skipped_gracefully_without_table(db, engine, helper):
    TF.run(db, [P1])                                           # helper present, table absent: never created
    assert "cygnus" in TF.astro_tags(db, P1)
    c = sqlite3.connect(db)
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'photo_annotations'").fetchone()
    c.close()


def test_annotation_error_does_not_lose_tags(db, engine, helper, monkeypatch):
    c = sqlite3.connect(db)
    c.execute(TABLE)
    c.commit()
    c.close()

    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(helper, "upsert_annotation", boom)
    TF.run(db, [P1])
    assert "cygnus" in TF.astro_tags(db, P1)


# ------------------------------------------------------------------ config ---

def test_config_defaults_valid_and_unknown_key_warns(caplog):
    e, w = ap.validate_config(dict(ap.DEFAULTS))
    assert e == [] and w == []
    e, w = ap.validate_config(dict(ap.DEFAULTS, bogus=1))
    assert e == [] and any("bogus" in x for x in w)


@pytest.mark.parametrize("bad", [{"min_confidence": 120}, {"solve_source": "tiff"}, {"tag_targets": ["cloud"]},
                                 {"tag_targets": []}, {"tag_levels": [9]}, {"tag_style": "fancy"},
                                 {"batch_min": 0}, {"enabled": "yes"}, {"roots": "C:/x"}, {"timeout_s": -1}])
def test_config_bad_values_are_errors(bad):
    e, _ = ap.validate_config(dict(ap.DEFAULTS, **bad))
    assert e, bad


def test_bad_config_disables_plugin(db, caplog):
    ap.configure({"engine": "X", "solve_source": "nonsense"}, db)
    cfg = ap.get_config()
    assert cfg["enabled"] is False and cfg["tag_targets"] == ["facet"]
    assert any("solve_source" in r.message for r in caplog.records)
    ap.configure({"engine": "X"}, db)
    assert ap.get_config()["enabled"] is True


def test_tag_targets_string_form():
    assert ap.normalize_tag_targets("facet, raw_xmp") == ["facet", "raw_xmp"]
    assert ap.validate_config(dict(ap.DEFAULTS, tag_targets="facet,raw_xmp"))[0] == []


def test_solve_source_and_overlay_scores_flags():
    assert ap.engine_flags({}, dict(ap.DEFAULTS, solve_source="auto", overlay_scores=True)) == \
        ["--solve-source", "auto", "--overlay-scores"]
    assert ap.engine_flags({}, dict(ap.DEFAULTS)) == []


# --------------------------------------------------------------------- xmp ---

EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Facet" / "exiftool" / "exiftool.exe"
needs_exiftool = pytest.mark.skipif(not EXE.exists(), reason="Facet exiftool not installed")


def _subject(sidecar):
    r = subprocess.run([str(EXE), "-j", "-charset", "utf8", "-XMP-dc:Subject", sidecar], capture_output=True,
                       text=True, encoding="utf-8")
    v = json.loads(r.stdout)[0].get("Subject", [])
    return v if isinstance(v, list) else [v]


@pytest.fixture
def raw_pair(tmp_path):
    (tmp_path / "DSC1.JPG").write_bytes(b"jpg")
    (tmp_path / "DSC1.ARW").write_bytes(b"raw")
    return str(tmp_path / "DSC1.JPG"), str(tmp_path / "DSC1.ARW.xmp")


def test_plan_keywords_never_removes_foreign_and_replaces_own():
    new, claim = ap.plan_keywords(["family", "orion", "jupiter"], ["jupiter"], ["orion", "saturn"])
    assert new == ["family", "orion", "saturn"] and claim == ["saturn"]   # orion belongs to the user: not claimed
    assert ap.plan_keywords(["family"], [], []) == (["family"], [])


@needs_exiftool
def test_xmp_merge_replace_and_cleanup(raw_pair):
    photo, sidecar = raw_pair
    cfg = dict(ap.DEFAULTS)
    Path(sidecar).write_text(ap.EMPTY_XMP.replace("\\ufeff", "\ufeff"), encoding="utf-8")
    subprocess.run([str(EXE), "-overwrite_original", "-XMP-dc:Subject+=family trip", "-XMP-dc:Subject+=orion",
                    sidecar], check=True, capture_output=True)
    dry = ap.sync_raw_xmp(photo, ["orion", "jupiter", "m31 andromeda galaxy"], cfg, dry_run=True)
    assert dry["added"] == ["jupiter", "m31 andromeda galaxy"] and _subject(sidecar) == ["family trip", "orion"]
    ap.sync_raw_xmp(photo, ["orion", "jupiter", "m31 andromeda galaxy"], cfg)
    assert _subject(sidecar) == ["family trip", "orion", "jupiter", "m31 andromeda galaxy"]
    again = ap.sync_raw_xmp(photo, ["orion", "jupiter", "m31 andromeda galaxy"], cfg)
    assert again.get("unchanged")
    ap.sync_raw_xmp(photo, ["orion", "saturn"], cfg)                  # replaces only what it wrote
    assert _subject(sidecar) == ["family trip", "orion", "saturn"]
    ap.sync_raw_xmp(photo, [], cfg)                                    # unsolved: its keywords go, the rest stay
    assert _subject(sidecar) == ["family trip", "orion"]


@needs_exiftool
def test_xmp_created_when_missing_and_sidecar_name_is_facet_convention(raw_pair):
    photo, sidecar = raw_pair
    assert not os.path.exists(sidecar)
    rep = ap.sync_raw_xmp(photo, ["cygnus", "deneb\u00e9"], dict(ap.DEFAULTS))
    assert rep["sidecar"].endswith("DSC1.ARW.xmp") and os.path.exists(sidecar)
    assert _subject(sidecar) == ["cygnus", "deneb\u00e9"]


def test_xmp_refuses_old_exiftool_and_missing_raw(raw_pair, monkeypatch, tmp_path):
    photo, sidecar = raw_pair
    monkeypatch.setattr(ap, "exiftool_version", lambda exe: (13, 27))
    assert ap.sync_raw_xmp(photo, ["x"], dict(ap.DEFAULTS)) is None and not os.path.exists(sidecar)
    (tmp_path / "ONLY.JPG").write_bytes(b"j")
    assert ap.raw_for(str(tmp_path / "ONLY.JPG")) is None
    assert ap.raw_for(photo).endswith("DSC1.ARW")


def test_tag_targets_raw_xmp_runs_sidecar_and_facet_only_skips_tags(db, engine, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "sync_raw_xmp", lambda p, t, cfg, dry=False: calls.append((p, t)) or {
        "sidecar": p + ".xmp", "added": t, "removed": [], "kept": []})
    cfg = dict(ap.get_config(), tag_targets=["facet", "raw_xmp"])
    out: dict = {}
    ap.process_paths([P1], cfg, db, 65, xmp_out=out)
    assert calls and "plate-solved" not in calls[0][1] and "cygnus" in calls[0][1] and P1 in out
    assert "cygnus" in TF.astro_tags(db, P1)
    calls.clear()
    ap.process_paths([P2], dict(cfg, tag_targets=["raw_xmp"]), db, 65)
    assert calls and TF.astro_tags(db, P2) == []                   # no manual tags written
