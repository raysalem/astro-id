"""Contract tests: the sidecar against a REAL Facet viewer API on a temporary library.

Marked ``facet_contract``; skipped unless a Facet checkout and its Python are found:

- ``FACET_DIR``     Facet checkout (default ``~/facet-wintest``)
- ``FACET_PYTHON``  Facet's interpreter (default: the Windows installer venv under %LOCALAPPDATA%\\Facet, else
                    ``<FACET_DIR>/venv``)

The server runs in Facet's own process (tests/facet_contract_server.py); astro-id only talks HTTP to it, exactly
like the sidecar does. Run just these with ``uv run pytest -m facet_contract``.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from astro_id.facet import sync as S
from astro_id.facet.client import FacetClient
from astro_id.facet.ledger import Ledger

pytestmark = pytest.mark.facet_contract

HERE = Path(__file__).resolve().parent
FACET_DIR = Path(os.environ.get("FACET_DIR") or Path.home() / "facet-wintest")


def _facet_python() -> Path | None:
    env = os.environ.get("FACET_PYTHON")
    cands = [Path(env)] if env else []
    if os.environ.get("LOCALAPPDATA"):
        cands.append(Path(os.environ["LOCALAPPDATA"]) / "Facet" / "venv" / "Scripts" / "python.exe")
    cands += [FACET_DIR / "venv" / "Scripts" / "python.exe", FACET_DIR / "venv" / "bin" / "python"]
    return next((c for c in cands if c.is_file()), None)


PY = _facet_python()
if not (FACET_DIR / "api" / "__init__.py").is_file() or PY is None:
    pytest.skip(f"no Facet checkout/interpreter (FACET_DIR={FACET_DIR}, FACET_PYTHON)", allow_module_level=True)

sys.path.insert(0, str(HERE))
import facet_contract_server as FS  # noqa: E402

N1, N2, N3 = (p["path"] for p in FS.PHOTOS[:3])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def facet(tmp_path_factory):
    work = tmp_path_factory.mktemp("facet-contract")
    port = _free_port()
    log = open(work / "server.log", "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.Popen([str(PY), str(HERE / "facet_contract_server.py"), "--facet-dir", str(FACET_DIR),
                             "--workdir", str(work), "--port", str(port)], stdout=log, stderr=subprocess.STDOUT,
                            env=env, cwd=str(FACET_DIR))
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            if proc.poll() is not None:
                pytest.fail("Facet test server exited:\n" + (work / "server.log").read_text(encoding="utf-8")[-3000:])
            try:
                if requests.get(url + "/health", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.5)
        else:
            pytest.fail("Facet test server did not start")
        facet.workdir = work
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def client(facet):
    # One login for the module: Facet rate-limits logins per client IP (429), so the sidecar keeps its token too.
    return FacetClient(facet, edition_password=FS.PASSWORD)


@pytest.fixture()
def ledger(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as led:
        yield led


def frame(solved=True, tags=("orion", "taurus")):
    fr = {"solved": solved, "objects": [{"tag": t, "confidence": 90, "level": 1, "taggable": True} for t in tags],
          "quality": {"version": 1, "detected": 1000, "fwhm_px": 5.0, "elongation": 1.1, "grade": "A"}}
    if solved:
        fr["annotations"] = {"version": 1, "source": "astro-id", "title": "Sky annotations",
                             "image": {"width": 6000, "height": 4000},
                             "groups": [{"id": "constellation", "label": "Constellations", "default": True}],
                             "score": {"label": "Confidence", "min": 0, "max": 100, "default": 65},
                             "items": [{"id": "orion", "label": "Orion", "group": "constellation", "score": 90,
                                        "shape": "lines", "segments": [[[1, 2], [30, 40]]], "label_at": [5, 5]}]}
    return fr


def test_login_version_and_feature_detection(client):
    assert client.login()
    v = client.server_version()
    assert v and v != "unknown"
    f = client.features()
    assert f["annotations_read"] is True              # Ray's stack has GET/DELETE /api/photo/annotations
    assert isinstance(f["annotations_put"], bool)     # PUT exists only once Facet PR-1 lands


def test_wrong_password_is_401(facet):
    with pytest.raises(Exception) as ei:
        FacetClient(facet, edition_password="nope").login()
    assert getattr(ei.value, "status", None) == 401


def test_listing_shape_category_prefix_and_raw_companion(client):
    got = client.list_photos(path_prefix=FS.PREFIX, category="astro")
    assert [p["path"] for p in got] == [N1, N2, N3]
    p1 = got[0]
    for key in ("gps_latitude", "gps_longitude", "date_taken", "manual_tags", "tags_list"):
        assert key in p1
    assert ("raw_companion" not in p1) or p1["raw_companion"] == "N1.ARW"
    one = client.photo(N2)
    assert one["date_manually_set"] == 1 and S.facet_hints(one)["time"] == "2019-11-05T09:33:10"
    assert client.photo("C:/astro-contract/none.jpg") is None
    assert len(client.list_photos(category="")) == 4


def test_tags_round_trip_through_batch_and_delete(client, ledger):
    ctx = S.SyncContext(client=client, ledger=ledger, annotations=False).prepare()
    client.batch_add_tag("my own", [N1])               # a user tag must survive
    S.apply_results(ctx, {N1: frame(tags=("orion", "stars")), N2: frame(solved=False)})
    p1, p2 = client.photo(N1), client.photo(N2)
    assert set(p1["manual_tags"]) == {"my own", "orion", "plate-solved"}    # 'stars' is an AI tag on N1
    assert p2["manual_tags"] == ["plate-solve failed"]
    assert sorted(ledger.tags(ctx.library, N1)) == ["orion", "plate-solved"]
    again = S.apply_results(ctx, {N1: frame(tags=("orion", "stars")), N2: frame(solved=False)})
    assert all(not r["add"] and not r["remove"] for r in again)
    S.apply_results(ctx, {N1: frame(tags=("taurus",))})
    assert set(client.photo(N1)["manual_tags"]) == {"my own", "taurus", "plate-solved"}
    S.remove_all(ctx)
    assert client.photo(N1)["manual_tags"] == ["my own"] and client.photo(N2)["manual_tags"] == []


def test_gps_write(client):
    r = client.put_gps(N3, 32.6, -116.3)
    assert r == {"gps_latitude": 32.6, "gps_longitude": -116.3}
    assert S.gps_source(client.photo(N3)) == "facet-manual"


def test_annotations_api_or_legacy(client, ledger, facet, tmp_path):
    ctx = S.SyncContext(client=client, ledger=ledger, annotations=True).prepare()
    if ctx.mode == "api":
        rep = S.apply_results(ctx, {N3: frame()})
        assert rep[0]["annotation"] == "stored"
        got = client.annotations(N3)
        assert got and got[0]["source"] == "astro-id" and got[0]["payload"]["quality"]["grade"] == "A"
    else:
        assert ctx.mode is None and "annotations skipped" in ctx.warnings[0]
        rep = S.apply_results(ctx, {N3: frame()})
        assert rep[0]["annotation"].startswith("skipped")
        assert client.annotations(N3) == []


def test_legacy_db_annotations_are_read_back_by_facet(client, ledger):
    if client.features()["annotations_put"]:
        pytest.skip("this Facet has PUT /api/photo/annotations; --legacy-db is never used")
    db = facet.workdir / "library.db"
    ctx = S.SyncContext(client=client, ledger=ledger, legacy_db=str(db), facet_dir=str(FACET_DIR)).prepare()
    assert ctx.mode == "legacy-db"
    rep = S.apply_results(ctx, {N1: frame()})
    assert rep[0]["annotation"] == "stored"
    got = client.annotations(N1)                       # Facet's own GET route reads what the legacy path wrote
    assert got[0]["payload"]["items"][0]["id"] == "orion" and got[0]["payload"]["quality"]["fwhm_px"] == 5.0
    S.apply_results(ctx, {N1: frame(solved=False)})
    assert client.annotations(N1) == []
    ctx.close()


def test_unknown_routes_are_absent_not_errors(client):
    # what feature detection relies on: a route that does not exist never answers 422/401/403
    assert client._probe("PUT", "/api/photo/no-such-route", json={}) in (404, 405)
