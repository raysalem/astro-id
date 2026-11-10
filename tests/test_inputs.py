from pathlib import Path

from astro_id.imageio import collect, select_inputs


def _touch(d: Path, *names):
    for n in names:
        (d / n).write_bytes(b"")


def test_raw_maps_to_same_stem_jpg(tmp_path):
    _touch(tmp_path, "DSC0001.ARW", "DSC0001.JPG", "DSC0002.ARW", "DSC0003.JPG", "IMG_9.CR3", "img_9.jpg",
           "notes.txt", "DSC0004.nef")
    names = [p.name for p in collect(tmp_path)]
    assert names == ["DSC0001.JPG", "DSC0002.ARW", "DSC0003.JPG", "DSC0004.nef", "img_9.jpg"]


def test_pairs_not_double_counted():
    out = select_inputs([Path("a/X.ARW"), Path("a/X.JPG"), Path("b/X.ARW")])
    assert [p.as_posix() for p in out] == ["a/X.JPG", "b/X.ARW"]


def test_single_raw_file_resolves_to_twin(tmp_path):
    _touch(tmp_path, "DSC0001.ARW", "DSC0001.JPG")
    assert collect(tmp_path / "DSC0001.ARW") == [tmp_path / "DSC0001.JPG"]


def test_report_csv_merges_by_file(tmp_path):
    from astro_id.pipeline import write_csv

    p = tmp_path / "astro-id-report.csv"
    write_csv(p, [{"file": "A.JPG", "object": "Orion"}, {"file": "A.JPG", "object": "Rigel"},
                  {"file": "B.JPG", "object": "Leo"}])
    write_csv(p, [{"file": "B.JPG", "object": "Jupiter"}])  # single-file run: only B is replaced
    import csv

    rows = [(r["file"], r["object"]) for r in csv.DictReader(open(p, newline="", encoding="utf-8"))]
    assert rows == [("A.JPG", "Orion"), ("A.JPG", "Rigel"), ("B.JPG", "Jupiter")]
