"""Astro session: time preview, folder config, job planning, JSON-lines events, tolerance end to end."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from astro_id import cli, session
from astro_id.session import SessionOptions, plan_jobs, preview_one, run_session, save_folder_config, time_summary
from test_synthetic import frame_dir, stub_astap  # noqa: F401  (fixtures)


def jpg(path: Path, camera_time="2019:11:05 09:31:59"):
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = camera_time
    Image.new("RGB", (64, 48), (10, 10, 10)).save(path, "JPEG", exif=exif)
    return path


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "night"
    d.mkdir()
    jpg(d / "A.JPG")
    jpg(d / "B.JPG")
    (d / "B.ARW").write_bytes(b"")
    (d / "astro-id.json").write_text(json.dumps({
        "clock_offset": "+2559d11h08m", "time_tolerance_min": 15, "timezone": "America/Los_Angeles",
        "site": {"name": "Dark site", "lat": 32.6, "lon": -116.3, "elev_m": 1100}}), encoding="utf-8")
    return d


def test_preview_uses_folder_config(folder):
    r = preview_one(str(folder / "A.JPG"), SessionOptions())
    assert r["camera_time"] == "2019-11-05T09:31:59"
    assert r["true_time"] == "2026-11-07T20:39:59-08:00"
    assert (r["window_start"], r["window_end"]) == ("2026-11-07T20:24:59-08:00", "2026-11-07T20:54:59-08:00")
    assert r["offset_source"] == "config" and r["time_source"] == "config" and r["tz_source"] == "config"
    assert r["site_source"] == "config" and r["site"]["name"] == "Dark site"
    assert r["raw_companion"] is None and r["solve_source"] == "jpg" and r["error"] is None


def test_preview_session_overrides_and_hints(folder):
    a, b = str(folder / "A.JPG"), str(folder / "B.JPG")
    opts = SessionOptions(offset="+2559d11h10m", tolerance_min=5, source="raw", tz="America/Denver",
                          site="40,-105", hints={a: {"site": "33.1,-116.0,900", "time": "2026-11-07T21:00:00"}})
    ra, rb = preview_one(a, opts), preview_one(b, opts)
    assert ra["time_source"] == "facet" and ra["true_time"] == "2026-11-07T21:00:00-07:00"
    assert ra["window_start"] == "2026-11-07T20:55:00-07:00" and ra["tolerance_min"] == 5
    assert ra["site_source"] == "photo" and ra["site"]["elev_m"] == 900
    assert ra["solve_source"] == "jpg" and any("no RAW companion" in w for w in ra["warnings"])
    assert rb["true_time"] == "2026-11-07T20:41:59-07:00" and rb["offset_source"] == "session"
    assert rb["site_source"] == "session" and rb["tz_source"] == "session"
    assert rb["raw_companion"].endswith("B.ARW") and rb["solve_source"] == "raw"


def test_preview_without_config_warns(tmp_path, monkeypatch):
    import astro_id.config as cfg

    monkeypatch.setattr(cfg, "find_config", lambda folder: None)
    r = preview_one(str(jpg(tmp_path / "X.JPG")), SessionOptions())
    assert r["time_source"] == "exif" and r["site_source"] == "none" and len(r["warnings"]) == 2
    assert preview_one(str(tmp_path / "missing.jpg"), SessionOptions())["error"]


def test_save_folder_config_merges_and_copies_parent(folder, tmp_path):
    sub = folder / "sub"
    sub.mkdir()
    opts = SessionOptions(offset="+2559d11h10m", tolerance_min=5, tz="America/Los_Angeles")
    written = save_folder_config([str(folder), str(sub), str(sub)], opts)
    assert len(written) == 2
    top = json.loads((folder / "astro-id.json").read_text(encoding="utf-8"))
    child = json.loads((sub / "astro-id.json").read_text(encoding="utf-8"))
    assert top["clock_offset"] == "+2559d11h10m" and top["time_tolerance_min"] == 5 and top["site"]["lat"] == 32.6
    assert child == top                                     # the parent's site comes along
    assert save_folder_config([str(folder)], opts) == []    # unchanged: not rewritten


def test_plan_jobs_groups_by_folder_and_site(folder):
    a, b = str(folder / "A.JPG"), str(folder / "B.JPG")
    raw = str(folder / "B.ARW")
    jobs = plan_jobs([a, b], SessionOptions())
    assert len(jobs) == 1 and [Path(f).name for f in jobs[0]["files"]] == ["A.JPG", "B.JPG"]
    jobs = plan_jobs([a, raw], SessionOptions(hints={a: {"time": "2026-11-07T21:00:00"}}))
    assert len(jobs) == 2
    assert jobs[0]["requested"] == [raw] and Path(jobs[0]["files"][0]).name == "B.JPG"   # RAW -> its JPG
    assert jobs[1]["time"] == "2026-11-07T21:00:00"
    jobs = plan_jobs([a, b], SessionOptions(hints={a: {"site": "1,2"}}))
    assert len(jobs) == 2


def _fake_frame(f, solved=True, fwhm=5.0, delta=None):
    return {"file": str(Path(f).resolve()).replace("\\", "/"), "solved": solved, "solver": "astap" if solved else None,
            "grade": "A" if solved else None, "stars": 300, "rms_px": 0.5, "objects": [
                {"name": "Orion", "tag": "orion", "confidence": 90, "taggable": True},
                {"name": "Rigel", "tag": "rigel", "confidence": 99, "taggable": False}],
            "time_tolerance_min": 5, "best_fit_delta_min": delta, "time_flag": None,
            "quality": {"fwhm_px": fwhm, "elongation": 1.1, "detected": 4000}}


def test_run_session_events_and_args(folder):
    a, b = str(folder / "A.JPG"), str(folder / "B.JPG")
    seen_args = []

    def fake_solve(files, args, stack_cfg, on_frame):
        seen_args.append(args)
        for f in reversed(files):   # completion order differs from input order
            on_frame(_fake_frame(f, fwhm=40.0 if f.name == "B.JPG" else 5.0, delta=1.5))
        return []
    events = []
    opts = SessionOptions(offset="+2559d11h08m", tolerance_min=5, source="jpg", tz="America/Los_Angeles")
    out = run_session([a, b], opts, events.append, solve_files_fn=fake_solve)
    args = seen_args[0]
    assert args.time_tolerance == "5m" and args.offset == "+2559d11h08m" and args.solve_source == "jpg"
    assert args.timezone == "America/Los_Angeles" and args.time is None
    kinds = [e["event"] for e in events]
    assert kinds == ["start", "photo", "photo", "done"]
    photos = {e["path"]: e for e in events if e["event"] == "photo"}
    assert photos[a]["status"] == "solved" and photos[a]["objects"] == 1 and photos[a]["index"] == 0
    assert "result" not in photos[a]
    done = events[-1]
    assert done["solved"] == 2 and done["failed"] == 0
    assert done["cull"]["frames"][b]["decision"] == "review" and done["cull"]["frames"][a]["decision"] == "keep"
    assert done["time"]["suggest_tolerance_min"] == 4 and done["time"]["best_fit_frames"] == 2
    assert out["results"][a]["solved"]
    json.dumps(events)   # JSON-serialisable


def test_run_session_job_failure_and_missing_file(folder):
    a = str(folder / "A.JPG")
    gone = str(folder / "GONE.JPG")

    def boom(files, args, stack_cfg, on_frame):
        raise RuntimeError("engine exploded")
    events = []
    run_session([a, gone], SessionOptions(), events.append, solve_files_fn=boom)
    photos = {e["path"]: e for e in events if e["event"] == "photo"}
    assert photos[gone]["error"] == "file not found" and "exploded" in photos[a]["error"]
    assert events[-1]["failed"] == 2


def test_run_session_hint_time_is_a_single_file_job(folder):
    a, b = str(folder / "A.JPG"), str(folder / "B.JPG")
    seen = []

    def fake_solve(files, args, stack_cfg, on_frame):
        seen.append((list(files), args.time, args.site))
        for f in files:
            on_frame(_fake_frame(f))
    run_session([a, b], SessionOptions(hints={a: {"time": "2026-11-07T21:00:00", "site": "33,-116,900"}}),
                None, solve_files_fn=fake_solve)
    assert ([Path(f).name for f in seen[1][0]], seen[1][1], seen[1][2]) == (["A.JPG"], "2026-11-07T21:00:00",
                                                                            "33,-116,900")
    assert seen[0][1] is None and [Path(f).name for f in seen[0][0]] == ["B.JPG"]


def test_time_summary():
    rs = [{"best_fit_delta_min": -3.2, "time_tolerance_min": 15}, {"best_fit_delta_min": None}, {"time_flag": "x"}]
    s = time_summary(rs, None)
    assert s["max_abs_delta_min"] == 3.2 and s["suggest_tolerance_min"] == 6 and s["outside_tolerance"] == 1
    assert time_summary(rs, 5)["suggest_tolerance_min"] is None   # already tighter than the suggestion
    assert time_summary([], 5)["best_fit_frames"] == 0


def test_validate_rejects_bad_options():
    for bad in (SessionOptions(offset="soon"), SessionOptions(tolerance_min=-1), SessionOptions(source="tiff"),
                SessionOptions(site="99,0"), SessionOptions(tz="Mars/Olympus"), SessionOptions(cull={"order": ["x"]}),
                SessionOptions(hints={"a": {"time": "yesterday"}})):
        with pytest.raises(ValueError):
            bad.validate()


def test_cli_preview_json_lines(folder, capsys):
    rc = cli.main(["session", "--preview", str(folder / "A.JPG"), "--tolerance-min", "5"])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 0 and [x["event"] for x in lines] == ["start", "preview", "done"]
    assert lines[1]["tolerance_min"] == 5


def test_cli_session_paths_file_and_bad_option(folder, capsys, tmp_path, monkeypatch):
    pf = tmp_path / "paths.json"
    pf.write_text(json.dumps({"paths": [str(folder / "A.JPG")], "hints": {}}), encoding="utf-8")
    monkeypatch.setattr(session, "run_session", lambda paths, opts, emit, save_config: emit(
        {"event": "done", "n": len(paths), "save": save_config, "tol": opts.tolerance_min}))
    assert cli.main(["session", "--paths-file", str(pf), "--tolerance-min", "5", "--save-folder-config"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"event": "done", "n": 1, "save": True, "tol": 5.0}
    assert cli.main(["session", str(folder / "A.JPG"), "--offset", "soon"]) == 2
    assert json.loads(capsys.readouterr().out)["event"] == "error"


def test_cli_cull(tmp_path, capsys):
    mf = tmp_path / "m.json"
    mf.write_text(json.dumps({"a": {"fwhm_px": 5, "detected": 900}, "b": {"fwhm_px": 30, "detected": 900}}),
                  encoding="utf-8")
    assert cli.main(["cull", "--metrics-file", str(mf), "--cull-rule", '{"order": ["focus"]}']) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["frames"]["b"]["decision"] == "review" and doc["frames"]["a"]["decision"] == "keep"
    assert cli.main(["cull", "--metrics-file", str(mf), "--cull-rule", '{"order": ["x"]}']) == 2


def test_session_end_to_end_honours_tolerance(frame_dir, stub_astap, tmp_path):  # noqa: F811
    """Real engine on the synthetic frame: a 5-minute window reaches the result, legend and quality."""
    events = []
    p = str(frame_dir / "SYN0001.JPG")
    out = run_session([p], SessionOptions(offset="+0m", tolerance_min=5, site="32.6064,-116.3239,1100",
                                          tz="America/Los_Angeles", no_fallback=True,
                                          overlay_dir=str(tmp_path / "ov"), include_results=True), events.append)
    fr = out["results"][p]
    assert fr["solved"] and fr["time_tolerance_min"] == 5 and fr["true_time"] == "2026-11-08T05:15:00-08:00"
    assert "5 min" in fr["annotations"]["legend"]
    q = fr["quality"]
    assert q["detected"] > 50 and q["matched"] == fr["stars"] and q["fwhm_px"] and q["grade"] in ("A", "B", "C")
    photo = next(e for e in events if e["event"] == "photo")
    assert photo["tolerance_min"] == 5 and photo["result"]["file"] == fr["file"] and photo["quality"] == q
    assert events[-1]["cull"]["frames"][p]["decision"] in ("keep", "review")


def test_timezone_flag_applies_to_exif_plus_offset(folder):
    from astro_id.config import resolve

    assert resolve(folder, None, None, None).timezone == "America/Los_Angeles"
    fc = resolve(folder, None, None, None, tz_flag="America/Denver")
    assert fc.timezone == "America/Denver" and fc.time_override is None


def test_save_folder_config_keeps_file_type_and_overlay(tmp_path):
    """The panel saves the folder's file type and overlay choices with the time settings (one folder, set once)."""
    d = tmp_path / "astro"
    d.mkdir()
    opts = SessionOptions(offset="+1h", tolerance_min=5, source="raw")
    save_folder_config([str(d)], opts, {"overlay": {"on": True, "lines": False, "scores": True}})
    saved = json.loads((d / "astro-id.json").read_text(encoding="utf-8"))
    assert saved["solve_source"] == "raw"
    assert saved["overlay"] == {"on": True, "lines": False, "scores": True}
    assert saved["time_tolerance_min"] == 5


def test_overlay_lines_flag_reaches_the_engine():
    from astro_id.session import _solve_args
    import inspect
    assert "lines" in inspect.signature(__import__("astro_id.overlay", fromlist=["draw_overlay"]).draw_overlay).parameters
    opts = SessionOptions(overlay_lines=False)
    args = _solve_args(Path("x.jpg"), opts, None, None)   # parsed `astro-id solve` namespace
    assert args.no_overlay_lines is True
    assert _solve_args(Path("x.jpg"), SessionOptions(), None, None).no_overlay_lines is False
