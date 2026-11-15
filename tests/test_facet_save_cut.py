"""B-P11: "Save to Facet" at a new cut must converge even when astro-id's ledger is behind Facet.

The real shape (GUI smoke G7c, :5003, 2026-11-15): Facet holds astro-id's object tags as plain rows with
``created_by='astro-id'`` (written by the old in-process plugin), and the ledger has a record for the photo with
``tags=[]``. Before the fix every row was treated as the user's: a save at any cut planned +0 -0.

- older contract that says who wrote a row (``get_photo`` ``manual_tag_sources``, Facet local/photo-page-layout):
  astro-id adopts its own rows and a new cut adds/removes object tags to match; the user's rows are never touched
  (its ``remove_tags`` deletes any row with that text, so the owner check is astro-id's);
- older contract that does not say (local/addons-annot): nothing is removed, and the save reports how many tags
  with an object's name it could not attribute;
- model D (Facet fix/review-all): ``get_photo`` lists only plugin summary rows, but ``remove_tags`` removes only the
  add-on's own rows, so when the ledger is behind astro-id offers its candidate names to ``remove_tags`` once and
  Facet keeps the user's.
"""
from __future__ import annotations

import threading
import time

import pytest

from astro_id.facet import serve as SV
from fake_facet import FakeFacet
from test_facet_addon import P1, TOKEN, addon_env, photos
from test_facet_model_d import NEW_TOOLS, OLD_TOOLS, calls, frame

ASTRO = "astro-id"


def seed(fake, owners: dict[str, str | None]):
    """P1 holds these manual tags, each written by ``owners[tag]`` (None = typed by the user)."""
    p = fake.photos[P1]
    p["manual_tags"] = sorted(owners)
    p["tag_created_by"] = {t: o for t, o in owners.items() if o}


@pytest.fixture()
def panel(request, tmp_path):
    """(fake, req, app) for an add-on panel on a fake Facet built with ``request.param`` (FakeFacet kwargs)."""
    import http.client
    import json

    from test_facet_addon import SECRET

    kw = dict(request.param)
    with FakeFacet(photos(), addon_token=TOKEN, **kw) as fake:
        args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                              "path_map": None})()
        server, box, aenv = SV.build_addon(args, addon_env(fake))
        app = SV.finish_addon(args, box, aenv)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            c.request(method, path, body=None if body is None else json.dumps(body),
                      headers={"X-Facet-Launch": SECRET, "Content-Type": "application/json"})
            r = c.getresponse()
            doc = json.loads(r.read() or b"null")
            c.close()
            assert r.status == 200, doc
            return doc
        try:
            yield fake, req, app
        finally:
            server.shutdown()
            server.server_close()
            app.ledger.close()


def solve(req, monkeypatch):
    from astro_id import session

    def fake_run(paths, opts, emit):
        done = {"event": "done", "solved": len(paths), "elapsed_s": 0.1, "time": {},
                "cull": {"reference": {}, "rule": {}, "frames": {p: {"decision": "keep", "reasons": []}
                                                                  for p in paths}}}
        emit(done)
        return {**done, "results": {p: frame() for p in paths}}
    monkeypatch.setattr(session, "run_session", fake_run)
    req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7", "category": "astro"})
    req("POST", "/ui/api/solve", {"paths": [P1], "tolerance_min": "5"})
    for _ in range(200):
        if not req("GET", "/ui/api/job?after=0")["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("solve did not finish")


def behind(app):
    """The ledger record the :5003 library had: the photo is known, its tags are not."""
    app.ledger.record(app.ctx.library, P1, tags=[], payload_hash=None, annotation_via="api", status="solved",
                      facet_version="unknown")


OLD_WITH_SOURCES = {"mcp_tools": OLD_TOOLS, "tag_sources": True}
OLD_NO_SOURCES = {"mcp_tools": OLD_TOOLS}
MODEL_D = {"mcp_tools": NEW_TOOLS, "model_d": True}


@pytest.mark.parametrize("panel", [OLD_WITH_SOURCES], indirect=True)
def test_new_cut_converges_when_the_ledger_lost_the_tags(panel, monkeypatch):
    """The G7c shape: rows created_by astro-id, ledger tags=[]. Fails before the fix (+0 -0, m42 stays)."""
    fake, req, app = panel
    seed(fake, {"orion": ASTRO, "m42": ASTRO, "plate-solved": ASTRO, "jupiter": None})
    behind(app)
    solve(req, monkeypatch)
    s = req("POST", "/ui/api/save", {"min_confidence": 40, "paths": [P1]})     # orion 90 + m42 50: all there
    assert (s["tags_added"], s["tags_removed"]) == (0, 0)
    assert app.ledger.get(app.ctx.library, P1)["tags"] == ["m42", "orion", "plate-solved"]   # adopted
    s = req("POST", "/ui/api/save", {"min_confidence": 65, "paths": [P1]})     # m42 (50) drops out
    assert (s["tags_added"], s["tags_removed"]) == (0, 1)
    assert fake.photos[P1]["manual_tags"] == ["jupiter", "orion", "plate-solved"]
    assert app.ledger.get(app.ctx.library, P1)["tags"] == ["orion", "plate-solved"]
    s = req("POST", "/ui/api/save", {"min_confidence": 40, "paths": [P1]})     # back down: added again
    assert (s["tags_added"], s["tags_removed"]) == (1, 0)
    assert fake.photos[P1]["manual_tags"] == ["jupiter", "m42", "orion", "plate-solved"]
    assert not any("jupiter" in a["tags"] for a in calls(fake, "remove_tags"))     # the user's tag: never offered


@pytest.mark.parametrize("panel", [OLD_WITH_SOURCES], indirect=True)
def test_a_tag_the_user_typed_with_the_same_text_is_never_removed(panel, monkeypatch):
    fake, req, app = panel
    seed(fake, {"orion": None, "m42": ASTRO, "plate-solved": ASTRO})
    behind(app)
    solve(req, monkeypatch)
    s = req("POST", "/ui/api/save", {"min_confidence": 95, "paths": [P1]})     # nothing at or above 95
    assert s["tags_removed"] == 1 and fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    assert not any("orion" in a["tags"] for a in calls(fake, "remove_tags"))


@pytest.mark.parametrize("panel", [OLD_NO_SOURCES], indirect=True)
def test_without_owner_information_nothing_is_removed_and_the_save_says_so(panel, monkeypatch):
    """An older Facet whose get_photo does not say who wrote a row, and whose remove_tags deletes any row: astro-id
    cannot tell its rows from the user's, so it removes none and reports them."""
    fake, req, app = panel
    seed(fake, {"orion": ASTRO, "m42": ASTRO, "plate-solved": ASTRO, "jupiter": None})
    behind(app)
    solve(req, monkeypatch)
    s = req("POST", "/ui/api/save", {"min_confidence": 65, "paths": [P1]})
    assert (s["tags_added"], s["tags_removed"]) == (0, 0) and not calls(fake, "remove_tags")
    assert s["tags_unknown_owner"] == 3                    # m42, orion, plate-solved: maybe astro-id's, maybe yours
    rep = s["reports"][0]
    assert rep["unknown_owner"] == ["m42", "orion", "plate-solved"]
    assert fake.photos[P1]["manual_tags"] == ["jupiter", "m42", "orion", "plate-solved"]


@pytest.mark.parametrize("panel", [OLD_NO_SOURCES], indirect=True)
def test_without_owner_information_a_fresh_ledger_still_converges(panel, monkeypatch):
    """The last-known-good path on the same Facet: what astro-id added itself is in the ledger and follows the cut."""
    fake, req, app = panel
    solve(req, monkeypatch)
    s = req("POST", "/ui/api/save", {"min_confidence": 40, "paths": [P1]})
    assert s["tags_added"] == 3 and s["tags_unknown_owner"] == 0
    s = req("POST", "/ui/api/save", {"min_confidence": 65, "paths": [P1]})
    assert s["tags_removed"] == 1 and fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]


@pytest.mark.parametrize("panel", [MODEL_D], indirect=True)
def test_model_d_removes_its_own_old_rows_when_the_ledger_is_behind(panel, monkeypatch):
    """Model D: get_photo does not list the old plain rows' writer, but remove_tags only removes the add-on's own:
    astro-id offers its names once, Facet keeps the user's."""
    fake, req, app = panel
    seed(fake, {"orion": ASTRO, "plate-solved": ASTRO, "m42": None, "jupiter": None})
    behind(app)
    solve(req, monkeypatch)
    s = req("POST", "/ui/api/save", {"min_confidence": 65, "paths": [P1]})
    p = fake.photos[P1]
    assert p["manual_tags"] == ["jupiter", "m42", "plate-solved"] and p["plugin_rows"] == {"plate-solved"}
    assert s["old_tags_removed"] == 2 and s["old_tags_yours"] == 0     # orion + plain plate-solved; m42 is not ours
    row = app.ledger.get(app.ctx.library, P1)
    assert row["tags"] == ["plate-solved"] and row["tag_kind"] == "plugin" and row["legacy_tags"] == []
    assert not any("jupiter" in a["tags"] for a in calls(fake, "remove_tags"))     # not an object name
    n = len(calls(fake, "remove_tags")), len(calls(fake, "add_tags"))
    s = req("POST", "/ui/api/save", {"min_confidence": 65, "paths": [P1]})         # converged: ledger is current
    assert (len(calls(fake, "remove_tags")), len(calls(fake, "add_tags"))) == n and s["old_tags_removed"] == 0
