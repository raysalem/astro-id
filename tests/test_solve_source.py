"""Engine side of solve_source: config parsing, RAW source choice, pipeline wiring."""
from __future__ import annotations

import json

import pytest

from astro_id import config as cfgmod
from astro_id import imageio, pipeline
from astro_id.imageio import ExifInfo
from astro_id.solve import Solution


def test_parse_solve_source():
    assert cfgmod.parse_solve_source(None) == "jpg" and cfgmod.parse_solve_source("AUTO") == "auto"
    with pytest.raises(ValueError):
        cfgmod.parse_solve_source("tiff")


def test_cli_rejects_bad_solve_source(tmp_path):
    from astro_id import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["solve", str(tmp_path), "--solve-source", "tiff"])
    assert e.value.code == 2


def test_solve_source_from_astro_id_json(tmp_path):
    (tmp_path / "astro-id.json").write_text(json.dumps({"solve_source": "raw"}))
    assert cfgmod.resolve(tmp_path, None, None, None).solve_source == "raw"
    assert cfgmod.resolve(tmp_path, None, None, None, solve_source_flag="jpg").solve_source == "jpg"


def test_choose_source(tmp_path, monkeypatch):
    jpg, raw = tmp_path / "a.JPG", tmp_path / "a.arw"
    jpg.write_bytes(b"j")
    ex = ExifInfo(None, None, None, 9504, 6336, None)
    assert imageio.choose_source(jpg, ex, "jpg") == (jpg, None)
    assert imageio.choose_source(jpg, ex, "raw") == (jpg, None)            # no twin: falls back
    raw.write_bytes(b"r")
    monkeypatch.setattr(imageio, "raw_crop_for", lambda r, w, h: (32, 20, w, h))
    assert imageio.choose_source(jpg, ex, "raw") == (raw, (32, 20, 9504, 6336))
    assert imageio.choose_source(jpg, ex, "auto") == (raw, (32, 20, 9504, 6336))
    assert imageio.choose_source(jpg, ex, "jpg") == (jpg, None)
    monkeypatch.setattr(imageio, "raw_crop_for", lambda r, w, h: None)    # mapping uncertain: keep the JPG
    assert imageio.choose_source(jpg, ex, "raw") == (jpg, None)


def test_process_passes_raw_source_and_crop(tmp_path, monkeypatch):
    jpg = tmp_path / "a.JPG"
    jpg.write_bytes(b"j")
    (tmp_path / "a.ARW").write_bytes(b"r")
    seen = {}
    monkeypatch.setattr(pipeline, "read_exif", lambda p: ExifInfo(None, None, None, 9504, 6336, None))
    monkeypatch.setattr(imageio, "raw_crop_for", lambda r, w, h: (32, 20, w, h))

    def fake(path, exif, use_fallback=True, epoch=2026.0, source=None, raw_crop=None):
        seen.update(path=path, source=source, crop=raw_crop)
        return Solution(False, error="x")
    monkeypatch.setattr(pipeline, "solve_image", fake)
    fr, _ = pipeline.process(jpg, None, None, None, None, False, False, solve_source="raw")
    assert seen["path"] == jpg and seen["source"].name == "a.ARW" and seen["crop"] == (32, 20, 9504, 6336)
    assert fr["solve_source"] == "raw" and fr["solved"] is False
