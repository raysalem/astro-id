"""`astro-id label`: the three --out modes, originals untouched, re-run skips, folder vs file, exit codes."""
import json
from pathlib import Path

import pytest

from astro_id import cli, label


def _photo(d: Path, name="A.JPG", data=b"ORIGINAL"):
    p = d / name
    p.write_bytes(data)
    return p


@pytest.fixture
def stub(monkeypatch):
    """Stub the solver: 'bad*' files fail; others write the overlay like the real pipeline."""
    calls = []

    def fake_process(path, offset, tol, site, overlay_dir, make_overlay, use_fallback, *a, **kw):
        calls.append(path.name)
        fr = {"file": str(path), "solved": not path.name.startswith("bad"), "solver": "astap", "grade": "A",
              "stars": 100, "rms_px": 0.4, "error": None if not path.name.startswith("bad") else "no stars found",
              "objects": [{"name": "Orion", "confidence": 90, "x": 1.0, "y": 2.0}], "annotations": {"version": 1}}
        if fr["solved"] and make_overlay:
            out = Path(overlay_dir) / f"{path.stem}_astro.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"LABELED")
        return fr, []

    monkeypatch.setattr(label, "process", fake_process)
    monkeypatch.setattr("astro_id.solve.find_astap", lambda: ("astap.exe", None))
    return calls


def run(*argv):
    return cli.main(["label", *map(str, argv)])


def test_labeled_default_writes_copy_only(tmp_path, stub):
    p = _photo(tmp_path)
    assert run(p) == 0
    assert (tmp_path / "A_astro.jpg").read_bytes() == b"LABELED"
    assert not (tmp_path / "A.astro.json").exists()
    assert p.read_bytes() == b"ORIGINAL"


def test_sidecar_only_no_image(tmp_path, stub):
    _photo(tmp_path)
    assert run(tmp_path / "A.JPG", "--out", "sidecar") == 0
    assert not (tmp_path / "A_astro.jpg").exists()
    d = json.loads((tmp_path / "A.astro.json").read_text(encoding="utf-8"))
    assert d["astro_id_sidecar"] == 1 and d["contract"] == 1 and d["solved"] is True
    assert d["objects"][0]["name"] == "Orion" and d["annotations"] == {"version": 1}


def test_both(tmp_path, stub):
    _photo(tmp_path)
    assert run(tmp_path, "--out", "both") == 0
    assert (tmp_path / "A_astro.jpg").exists() and (tmp_path / "A.astro.json").exists()


def test_folder_skips_own_outputs_and_rerun_skips(tmp_path, stub, capsys):
    _photo(tmp_path, "A.JPG")
    _photo(tmp_path, "B.JPG")
    assert run(tmp_path) == 0
    assert sorted(stub) == ["A.JPG", "B.JPG"]
    stub.clear()
    assert run(tmp_path) == 0          # outputs exist: nothing solved, still success
    assert stub == []
    out = capsys.readouterr().out
    assert "skipped" in out and "2 already done" in out


def test_non_recursive_by_default_and_recursive(tmp_path, stub):
    sub = tmp_path / "sub"
    sub.mkdir()
    _photo(tmp_path, "A.JPG")
    _photo(sub, "B.JPG")
    assert run(tmp_path) == 0 and stub == ["A.JPG"]
    stub.clear()
    assert run(tmp_path, "--recursive") == 0 and stub == ["B.JPG"]
    assert (sub / "B_astro.jpg").exists()


def test_to_folder_leaves_source_folder_clean(tmp_path, stub):
    src, dst = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    _photo(src)
    assert run(src, "--to", dst, "--out", "both") == 0
    assert (dst / "A_astro.jpg").exists() and (dst / "A.astro.json").exists()
    assert sorted(p.name for p in src.iterdir()) == ["A.JPG"]


def test_unwritable_folder_hints_to(tmp_path, stub, monkeypatch, capsys):
    _photo(tmp_path)
    monkeypatch.setattr(label, "_writable", lambda d: False)
    assert run(tmp_path) == 1
    assert "--to" in capsys.readouterr().out and stub == []


def test_exit_codes(tmp_path, stub, monkeypatch, capsys):
    assert run(tmp_path / "nope") == 2                       # missing path
    assert run(tmp_path) == 2                                # empty folder
    _photo(tmp_path, "bad1.JPG")
    assert run(tmp_path) == 1                                # nothing labeled
    assert "not solved" in capsys.readouterr().out
    _photo(tmp_path, "good.JPG")
    assert run(tmp_path) == 0                                # one labeled, one failed
    assert run(tmp_path / "good.JPG", "--offset", "bogus") == 2
    monkeypatch.setattr("astro_id.solve.find_astap", lambda: (None, None))
    assert run(tmp_path / "good.JPG") == 2
    assert "astro-id check" in capsys.readouterr().out


def test_time_note_without_site(tmp_path, stub, capsys):
    _photo(tmp_path)
    run(tmp_path)
    assert "planets and the Moon" in capsys.readouterr().out


def test_progress_and_summary_lines(tmp_path, stub, capsys):
    _photo(tmp_path)
    run(tmp_path)
    out = capsys.readouterr().out
    assert "1/1 A.JPG" in out and "1 objects" in out and "A_astro.jpg" in out and "Done: 1 labeled" in out
