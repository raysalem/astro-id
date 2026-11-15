"""Contract test: astro-id's model D writer against a REAL model D Facet (fix/review-all) over /mcp.

Marked ``facet_contract``. Skipped unless ``FACET_DIR`` (default ``~/facet-review-all``) is a Facet checkout whose
add-on tools include ``set_annotations_batch`` (model D), and its Python is found (``FACET_PYTHON`` or the Windows
installer venv). The server runs in Facet's own process on a temporary library (tests/facet_contract_server.py
``--addon``: addons.enabled + an external astro-id manifest); the test mints the add-on token the documented way
(edition login, ``POST /api/addons/astro-id/token``) and then talks only MCP with it, like the add-on.

What it pins (test audit P3, 2026-11-15): the migration removes only astro-id's own old per-object rows, never a
tag the user typed (also one with the same text); summary tags land as plugin rows (<= 3); names become searchable
detections; rows written through the password/command-line path are reported as the user's (``legacy``) and kept;
a second save writes nothing; the old-Facet fallback is not taken.

    FACET_DIR=<path to a Facet checkout> uv run pytest -m facet_contract tests/test_facet_contract_model_d.py
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
from astro_id.facet.ledger import Ledger, library_key
from astro_id.facet.mcp_client import McpFacetClient

pytestmark = pytest.mark.facet_contract

HERE = Path(__file__).resolve().parent
FACET_DIR = Path(os.environ.get("FACET_DIR") or Path.home() / "facet-review-all")


def _facet_python() -> Path | None:
    env = os.environ.get("FACET_PYTHON")
    cands = [Path(env)] if env else []
    if os.environ.get("LOCALAPPDATA"):
        cands.append(Path(os.environ["LOCALAPPDATA"]) / "Facet" / "venv" / "Scripts" / "python.exe")
    cands += [FACET_DIR / "venv" / "Scripts" / "python.exe", FACET_DIR / "venv" / "bin" / "python"]
    return next((c for c in cands if c.is_file()), None)


def _model_d_checkout() -> bool:
    auth = FACET_DIR / "api" / "addons" / "auth.py"
    return auth.is_file() and "set_annotations_batch" in auth.read_text(encoding="utf-8", errors="replace")


PY = _facet_python()
if PY is None or not _model_d_checkout():
    pytest.skip(f"no model D Facet checkout/interpreter (FACET_DIR={FACET_DIR}, FACET_PYTHON)",
                allow_module_level=True)

sys.path.insert(0, str(HERE))
import facet_contract_server as FS  # noqa: E402

N1, N2, N3 = (p["path"] for p in FS.PHOTOS[:3])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def facet(tmp_path_factory):
    work = tmp_path_factory.mktemp("facet-contract-d")
    port = _free_port()
    log = open(work / "server.log", "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.Popen([str(PY), str(HERE / "facet_contract_server.py"), "--facet-dir", str(FACET_DIR),
                             "--workdir", str(work), "--port", str(port), "--addon"], stdout=log,
                            stderr=subprocess.STDOUT, env=env, cwd=str(FACET_DIR))
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
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def edition(facet):
    """An edition session (the user): Bearer JWT for /api."""
    r = requests.post(facet + "/api/auth/edition/login", json={"password": FS.PASSWORD}, timeout=30)
    assert r.status_code == 200, r.text
    s = requests.Session()
    s.trust_env = False
    s.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return s


@pytest.fixture(scope="module")
def token(facet, edition):
    r = edition.post(facet + "/api/addons/astro-id/token", timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["level"] == "edition"
    return r.json()["token"]


def user_tag(facet, edition, tag, paths):
    """What the user (or astro-id's password / command-line path) writes: a plain row, created_by NULL."""
    r = edition.post(facet + "/api/photos/batch_manual_tags",
                     json={"photo_paths": paths, "tag": tag, "action": "add"}, timeout=30)
    assert r.status_code == 200, r.text


def frame(names=("Orion", "M42")):
    objs = [{"name": n, "tag": n, "confidence": 90, "level": 1, "taggable": True} for n in names]
    items = [{"id": n.lower(), "label": n, "group": "constellation", "score": 90, "shape": "point", "x": 10.0,
              "y": 20.0, "r": 5.0} for n in names]
    return {"solved": True, "objects": objs,
            "quality": {"version": 1, "detected": 1000, "fwhm_px": 5.0, "elongation": 1.1, "grade": "A"},
            "annotations": {"version": 1, "source": "astro-id", "title": "Sky annotations",
                            "image": {"width": 6000, "height": 4000},
                            "groups": [{"id": "constellation", "label": "Constellations", "default": True}],
                            "score": {"label": "Confidence", "min": 0, "max": 100, "default": 65}, "items": items}}


def tags_of(client, path):
    """(plain rows, plugin rows): Facet lists plugin rows in ``manual_tags`` too, and in ``plugin_tags``."""
    p = client.photo(path)
    plugin = sorted(t["tag"] for t in p.get("plugin_tags") or [] if t.get("source") == "astro-id")
    return sorted(set(p.get("manual_tags") or []) - set(plugin)), plugin


def test_model_d_migration_on_a_real_facet(facet, edition, token, tmp_path):
    client = McpFacetClient(facet, token=token)
    f = client.features()
    assert f["model_d"] and f["plugin_tags"] and f["annotations_batch"] and "uncertain" not in f
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        ctx = S.SyncContext(client=client, ledger=ledger, addon=True).prepare()
        assert ctx.model_d and ctx.annotations_batch and ctx.mode == "api"     # (6) no old-Facet fallback
        lib = library_key(facet)
        # What an older Facet left. N1: astro-id's own per-object rows (add-on token, no plugin flag: created_by =
        # astro-id), plus a tag the user typed. N2: the same names written through the password path (created_by
        # NULL) that the ledger lists as astro-id's. N3: the user typed 'orion', the ledger knows nothing of it.
        for t in ("orion", "m42", "plate-solved"):
            client.batch_add_tag(t, [N1])
        client.batch_add_tag("plate-solved", [N2])
        user_tag(facet, edition, "taurus", [N1])
        for t in ("orion", "m42"):
            user_tag(facet, edition, t, [N2])
        user_tag(facet, edition, "orion", [N3])
        ledger.record(lib, N1, tags=["orion", "m42", "plate-solved"], payload_hash=None, annotation_via=None,
                      status="solved", facet_version="old")
        ledger.record(lib, N2, tags=["orion", "m42", "plate-solved"], payload_hash=None, annotation_via=None,
                      status="solved", facet_version="old")
        # Facet's own guard first: an add-on cannot remove the user's row of the same text.
        assert client.batch_remove_tag("orion", [N3]) == {"affected": 0, "skipped_foreign": 1}

        reps = {r["path"]: r for r in S.apply_results(ctx, {N1: frame(), N2: frame(), N3: frame()})}
        assert not any(r.get("error") for r in reps.values())
        # (1) only astro-id's own rows went; (2) the status tag is a plugin row
        assert tags_of(client, N1) == (["taurus"], ["plate-solved"])
        assert reps[N1]["migrated"] == ["m42", "orion", "plate-solved"] and reps[N1]["legacy"] == []
        # (4) the password-path rows stay and are reported as the user's, once
        assert tags_of(client, N2) == (["m42", "orion"], ["plate-solved"])
        assert reps[N2]["legacy"] == ["m42", "orion"] and reps[N2]["migrated"] == ["plate-solved"]
        assert ledger.get(lib, N2)["legacy_tags"] == ["m42", "orion"]
        assert tags_of(client, N3) == (["orion"], ["plate-solved"]) and reps[N3]["legacy"] == []
        # (3) the names are searchable detections
        found = {r["path"] for r in client.list_photos(extra={"detected": "orion"}, category=None)}
        assert found == {N1, N2, N3}
        dets = client.call("get_detections", {"path": N1})["detections"]
        assert sorted(d["label"] for d in dets) == ["m42", "orion"]
        # (7) the sky labels read back as a summary
        summ = client.annotations_summary(N1)
        assert summ and summ[0]["items"] == 2 and summ[0]["indexed"] == 2

        # (5) a second save writes nothing
        writes = []
        for name in ("batch_add_tag", "batch_remove_tag", "put_annotations_batch", "put_annotation",
                     "delete_annotation", "delete_tag"):
            orig = getattr(client, name)
            setattr(client, name, lambda *a, _o=orig, _n=name, **k: (writes.append(_n), _o(*a, **k))[1])
        again = S.apply_results(ctx, {N1: frame(), N2: frame(), N3: frame()})
        assert writes == []
        assert all(r["annotation"] == "unchanged" and not r["add"] and not r["remove"] for r in again)
        assert {r["path"]: r["legacy"] for r in again}[N2] == ["m42", "orion"]
