"""Undo of "Save to Facet" (``sync.undo``, ``POST api/undo``): removes only what astro-id wrote (its status tags,
object tags and sky labels, per the ledger), in both Facet modes: model D (plugin summary tags + indexed sky labels)
and an older Facet (plain per-object tags). Dry run writes nothing; a second undo changes nothing; refused while
busy; a partial failure leaves the ledger saying exactly what is still there."""
from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from astro_id.facet import serve as SV
from astro_id.facet import sync as S
from fake_facet import FakeFacet
from test_facet_addon import P1, P2, P3, SECRET, TOKEN, addon_env, photos
from test_facet_model_d import NEW_TOOLS, OLD_TOOLS, calls, ctx_for, frame

WRITES = ("add_tags", "remove_tags", "set_annotations", "set_annotations_batch", "clear_annotations")


def writes(fake):
    return [(n, a) for n, a in fake.mcp_calls if n in WRITES]


@pytest.fixture(params=["model_d", "old"])
def fake(request):
    d = request.param == "model_d"
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=NEW_TOOLS if d else OLD_TOOLS, model_d=d) as f:
        f.is_d = d
        yield f


@pytest.fixture()
def new_fake():
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=NEW_TOOLS, model_d=True) as f:
        yield f


@pytest.fixture()
def old_fake():
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=OLD_TOOLS) as f:
        yield f


def user_tag(fake, path, tag):
    """A tag the user typed in Facet (created_by the user, not the add-on)."""
    p = fake.photos[path]
    p["manual_tags"] = sorted(set(p["manual_tags"]) | {tag})
    p.setdefault("tag_created_by", {})[tag] = "user"


# ------------------------------------------------------------- the writer ---

def test_undo_removes_only_astro_ids_rows(fake, tmp_path):
    user_tag(fake, P1, "orion")              # the user's own tag with the same word as an object tag
    user_tag(fake, P1, "my trip")
    ctx = ctx_for(fake, tmp_path)
    assert ctx.model_d is fake.is_d
    S.apply_results(ctx, {P1: frame(), P2: frame(solved=False)})
    assert (P1, "astro-id") in fake.annotations
    out = S.undo(ctx, [P1, P2])
    assert fake.photos[P1]["manual_tags"] == ["my trip", "orion"]       # the user's rows stay
    assert fake.photos[P2]["manual_tags"] == ["night"]
    assert not fake.annotations and P1 not in fake.detections
    assert out["photos"] == 2 and out["status_tags"] == 2 and out["sky_labels"] == 1
    # model D: Orion is an object tag (indexed name in the sky labels); older Facet: the user had "orion" first, so
    # astro-id never claimed it and wrote no object tag of its own
    assert out["object_tags"] == (1 if fake.is_d else 0)
    assert out["not_removed"]["total"] == 0
    assert ctx.ledger.rows(ctx.library) == []


def test_old_facet_per_object_tags_are_object_tags(old_fake, tmp_path):
    ctx = ctx_for(old_fake, tmp_path)
    S.apply_results(ctx, {P1: frame()})
    assert old_fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    d = S.undo(ctx, [P1], dry_run=True)
    assert (d["object_tags"], d["status_tags"], d["sky_labels"], d["photos"]) == (1, 1, 1, 1)
    S.undo(ctx, [P1])
    assert old_fake.photos[P1]["manual_tags"] == [] and not old_fake.annotations


def test_dry_run_writes_nothing(fake, tmp_path):
    ctx = ctx_for(fake, tmp_path)
    S.apply_results(ctx, {P1: frame(), P2: frame(solved=False)})
    before = (json.dumps(fake.photos, default=sorted, sort_keys=True), dict(fake.annotations),
              len(writes(fake)), ctx.ledger.rows(ctx.library))
    d = S.undo(ctx, [P1, P2], dry_run=True)
    after = (json.dumps(fake.photos, default=sorted, sort_keys=True), dict(fake.annotations),
             len(writes(fake)), ctx.ledger.rows(ctx.library))
    assert before == after
    assert d["dry_run"] and d["photos"] == 2 and d["status_tags"] == 2 and d["sky_labels"] == 1


def test_second_undo_changes_nothing(fake, tmp_path):
    ctx = ctx_for(fake, tmp_path)
    S.apply_results(ctx, {P1: frame()})
    S.undo(ctx, [P1])
    n = len(writes(fake))
    again = S.undo(ctx, [P1])
    assert len(writes(fake)) == n
    assert (again["photos"], again["object_tags"], again["status_tags"], again["sky_labels"]) == (0, 0, 0, 0)
    assert again["recorded"] == 0


def test_only_the_given_paths(fake, tmp_path):
    ctx = ctx_for(fake, tmp_path)
    S.apply_results(ctx, {P1: frame(), P2: frame()})
    S.undo(ctx, [P2])
    assert "plate-solved" in fake.photos[P1]["manual_tags"] and (P1, "astro-id") in fake.annotations
    assert "plate-solved" not in fake.photos[P2]["manual_tags"] and (P2, "astro-id") not in fake.annotations
    assert [r["path"] for r in ctx.ledger.rows(ctx.library)] == [P1]


def test_model_d_user_owned_rows_are_never_removed(new_fake, tmp_path):
    """Rows the ledger lists but Facet says another writer owns (legacy: written through the user path) stay and
    are reported, never forced."""
    ctx = ctx_for(new_fake, tmp_path)
    p = new_fake.photos[P1]
    p["manual_tags"] = sorted(["orion", "m42", "plate-solved"])
    p["tag_created_by"] = {"orion": "user", "m42": "user", "plate-solved": "astro-id"}
    ctx.ledger.record(ctx.library, P1, tags=["orion", "m42", "plate-solved"], payload_hash="old",
                      annotation_via="api", status="solved", facet_version="old")
    S.apply_results(ctx, {P1: frame()})                      # orion/m42 become legacy: the user's now
    assert ctx.ledger.get(ctx.library, P1)["legacy_tags"] == ["m42", "orion"]
    d = S.undo(ctx, [P1], dry_run=True)
    assert d["not_removed"]["yours"] == 2 and d["status_tags"] == 1
    n = len(calls(new_fake, "remove_tags"))
    out = S.undo(ctx, [P1])
    assert sorted(p["manual_tags"]) == ["m42", "orion"]
    assert out["not_removed"]["yours"] == 2 and "yours" in out["why"]
    assert [a["tags"] for a in calls(new_fake, "remove_tags")[n:]] == [["plate-solved"]]   # never tried on theirs


def test_model_d_row_another_writer_owns_is_reported(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    S.apply_results(ctx, {P1: frame()})
    new_fake.photos[P1]["tag_created_by"]["plate-solved"] = "someone"   # another writer took the row over
    new_fake.photos[P1]["plugin_rows"].discard("plate-solved")
    out = S.undo(ctx, [P1])
    assert "plate-solved" in new_fake.photos[P1]["manual_tags"]
    assert out["not_removed"]["other_writer"] == 1 and out["status_tags"] == 0 and out["sky_labels"] == 1
    assert ctx.ledger.get(ctx.library, P1) is None              # nothing of astro-id's is left to track


def test_partial_failure_leaves_the_ledger_consistent(fake, tmp_path):
    ctx = ctx_for(fake, tmp_path)
    S.apply_results(ctx, {P1: frame(), P2: frame(solved=False)})
    real = ctx.client.batch_remove_tag

    def flaky(tag, paths):
        if tag == "plate-solved":
            raise ConnectionError("Facet restarted")
        return real(tag, paths)
    ctx.client.batch_remove_tag = flaky
    out = S.undo(ctx, [P1, P2])
    # P2's status tag went; P1's did not (the call failed) but its sky labels did
    assert "plate-solve failed" not in fake.photos[P2]["manual_tags"]
    assert "plate-solved" in fake.photos[P1]["manual_tags"] and (P1, "astro-id") not in fake.annotations
    assert out["not_removed"]["failed"] == 1 and "failed" in out["why"]
    row = ctx.ledger.get(ctx.library, P1)
    assert row["tags"] == ["plate-solved"] and row["payload_hash"] is None
    assert ctx.ledger.get(ctx.library, P2) is None
    # Facet is back: Undo again finishes it, and only it
    ctx.client.batch_remove_tag = real
    again = S.undo(ctx, [P1, P2])
    assert again["status_tags"] == 1 and again["sky_labels"] == 0 and again["photos"] == 1
    assert "plate-solved" not in fake.photos[P1]["manual_tags"] and ctx.ledger.rows(ctx.library) == []


def test_failed_sky_label_removal_stays_recorded(fake, tmp_path):
    ctx = ctx_for(fake, tmp_path)
    S.apply_results(ctx, {P1: frame()})
    real = ctx.client.delete_annotation
    ctx.client.delete_annotation = lambda path, source: (_ for _ in ()).throw(ConnectionError("down"))
    out = S.undo(ctx, [P1])
    assert out["sky_labels"] == 0 and out["not_removed"]["failed"] == 1
    row = ctx.ledger.get(ctx.library, P1)
    assert row["payload_hash"] and row["tags"] == [] and (P1, "astro-id") in fake.annotations
    ctx.client.delete_annotation = real
    assert S.undo(ctx, [P1])["sky_labels"] == 1 and ctx.ledger.get(ctx.library, P1) is None


def test_no_annotation_route_keeps_sky_labels_and_says_so(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    S.apply_results(ctx, {P1: frame()})
    ctx2 = ctx_for(new_fake, tmp_path, annotations=False)      # e.g. a token without annotations.write
    ctx2.ledger = ctx.ledger
    out = S.undo(ctx2, [P1])
    assert out["sky_labels"] == 0 and out["not_removed"]["no_route"] == 1 and (P1, "astro-id") in new_fake.annotations
    assert ctx.ledger.get(ctx.library, P1)["payload_hash"]       # still recorded: a later undo can remove it


# ------------------------------------------------------------- the panel ---

def _addon(fake, tmp_path):
    args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                          "path_map": None})()
    server, box, aenv = SV.build_addon(args, addon_env(fake))
    app = SV.finish_addon(args, box, aenv)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        h = {"X-Facet-Launch": SECRET, "Content-Type": "application/json"}
        c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        doc = json.loads(r.read() or b"null")
        c.close()
        return r.status, doc
    return server, app, req


@pytest.fixture()
def panel(fake, tmp_path, monkeypatch):
    from astro_id import session

    def fake_run(paths, opts, emit):
        done = {"event": "done", "solved": len(paths), "elapsed_s": 0.1, "time": {},
                "cull": {"reference": {}, "rule": {}, "frames": {p: {"decision": "keep", "reasons": []}
                                                                  for p in paths}}}
        emit(done)
        return {**done, "results": {p: frame() for p in paths}}
    monkeypatch.setattr(session, "run_session", fake_run)
    server, app, req = _addon(fake, tmp_path)
    req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7"})
    assert req("POST", "/ui/api/solve", {"paths": [P1, P2], "tolerance_min": "5"})[0] == 200
    for _ in range(200):
        if not req("GET", "/ui/api/job?after=0")[1]["running"]:
            break
        time.sleep(0.02)
    yield req, app, fake
    server.shutdown()
    server.server_close()
    app.ledger.close()


def test_panel_dry_run_then_undo_then_save_again(panel):
    req, app, fake = panel
    user_tag(fake, P1, "my trip")
    assert req("POST", "/ui/api/save", {"min_confidence": 65})[0] == 200
    n = len(writes(fake))
    st, d = req("POST", "/ui/api/undo", {"dry_run": True})      # default: everything loaded
    assert st == 200 and d["dry_run"] and d["photos"] == 2 and d["status_tags"] == 2 and d["sky_labels"] == 2
    assert d["object_tags"] == 2 and d["names_as"] == ("searchable" if fake.is_d else "tags")
    assert len(writes(fake)) == n                                  # the dry run wrote nothing
    st, d = req("POST", "/ui/api/undo", {"paths": [P1, P2]})
    assert st == 200 and d["photos"] == 2 and d["object_tags"] == 2
    assert fake.photos[P1]["manual_tags"] == ["my trip"] and not fake.annotations
    assert app.job.results and not app.job.saved_paths              # results kept, nothing counts as saved
    st, d = req("POST", "/ui/api/undo", {"paths": [P1, P2]})        # idempotent
    assert st == 200 and d["photos"] == 0 and d["recorded"] == 0
    st, s = req("POST", "/ui/api/save", {"min_confidence": 65})    # Save again writes them back
    assert st == 200 and s["photos"] == 2 and "plate-solved" in fake.photos[P1]["manual_tags"]


def test_panel_undo_refused_while_busy(panel):
    req, app, fake = panel
    req("POST", "/ui/api/save", {"min_confidence": 65})
    for busy, word in (("solve", "plate solve"), ("save", "save to Facet"), ("undo", "undo")):
        if busy == "solve":
            app.job.running = True
        else:
            app.writing = busy
        st, d = req("POST", "/ui/api/undo", {"dry_run": True})
        assert st == 400 and word in d["error"]
        app.job.running, app.writing = False, None
    app.export_job = {"running": True}
    st, d = req("POST", "/ui/api/undo", {"paths": [P1]})
    assert st == 400 and "export" in d["error"]
    app.export_job = {}
    assert "plate-solved" in fake.photos[P1]["manual_tags"]        # nothing was removed while refused
    app.writing = "undo"                                           # and a save waits for an undo
    st, d = req("POST", "/ui/api/save", {"min_confidence": 65})
    assert st == 400 and "undo" in d["error"]
    app.writing = None


def test_panel_undo_validates_paths(panel):
    req, _app, _fake = panel
    assert req("POST", "/ui/api/undo", {"paths": "x"})[0] == 400
    assert req("POST", "/ui/api/undo", {"paths": []})[0] == 400
    st, d = req("POST", "/ui/api/undo", {"paths": [P3], "dry_run": True})   # nothing recorded there
    assert st == 200 and d["photos"] == 0 and d["recorded"] == 0


def test_restart_waits_for_a_save_or_undo():
    assert SV.restart_decision(True, True, "undo") == {"restart": False, "reason": "busy", "busy": "undo"}
