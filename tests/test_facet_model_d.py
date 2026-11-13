"""Facet's model D add-on contract (Facet fix/review-all, docs/CONFIGURATION.md "Add-ons"; PLUGIN_MCP_REVIEW §3):
summary tags with ``plugin: true``, names as indexed detections in the annotation payload, ``set_annotations_batch``,
one MCP session per job, the migration of earlier per-object tags, ``get_annotations`` ``summary: true`` for the
sky-label controls, and the fallback to per-object tags on an older Facet (status without ``features``)."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from astro_id.facet import serve as SV
from astro_id.facet import sync as S
from astro_id.facet.client import FacetError, connect
from astro_id.facet.ledger import Ledger
from astro_id.facet.mcp_client import McpFacetClient
from fake_facet import FakeFacet
from fake_facet_mcp import ANNOTATION_TOOLS, ASTRO_TOOLS, MODEL_D_TOOLS
from test_facet_addon import P1, P2, ROOT, TOKEN, addon_env, photos

NEW_TOOLS = set(ASTRO_TOOLS | ANNOTATION_TOOLS | MODEL_D_TOOLS)
OLD_TOOLS = set(ASTRO_TOOLS | ANNOTATION_TOOLS)


def frame(solved=True):
    """One frame result: Orion at 90 (saved at the default cut 65), M42 at 50 (below it), Betelgeuse covered by
    Orion (``taggable`` false)."""
    if not solved:
        return {"solved": False, "objects": []}
    objs = [{"name": "Orion", "tag": "orion", "confidence": 90, "level": 1, "taggable": True},
            {"name": "M42", "tag": "m42", "confidence": 50, "level": 3, "taggable": True},
            {"name": "Betelgeuse", "tag": "betelgeuse", "confidence": 95, "level": 5, "taggable": False,
             "member_of": "orion"}]
    items = [{"id": "orion", "label": "Orion", "group": "constellation", "score": 90, "shape": "lines",
              "segments": [[[0, 0], [5, 5]]]},
             {"id": "m42", "label": "M42", "group": "catalog", "score": 50, "shape": "circle", "x": 3, "y": 3, "r": 12},
             {"id": "betelgeuse", "label": "Betelgeuse", "group": "star", "score": 95, "shape": "point", "x": 1,
              "y": 1}]
    return {"solved": True, "objects": objs,
            "annotations": {"version": 1, "source": "astro-id", "image": {"width": 10, "height": 10},
                            "groups": [{"id": "constellation", "label": "Constellations", "default": True},
                                       {"id": "catalog", "label": "Messier / NGC / IC", "default": True},
                                       {"id": "star", "label": "Stars", "default": False}],
                            "score": {"label": "Confidence", "min": 0, "max": 100, "default": 65}, "items": items}}


def ctx_for(fake, tmp_path, **kw) -> S.SyncContext:
    client = connect(fake.url, token=TOKEN)
    ledger = Ledger(tmp_path / "l.sqlite")
    return S.SyncContext(client=client, ledger=ledger, addon=True, **kw).prepare()


def calls(fake, tool):
    return [a for n, a in fake.mcp_calls if n == tool]


@pytest.fixture()
def new_fake():
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=NEW_TOOLS, model_d=True) as f:
        yield f


@pytest.fixture()
def old_fake():
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=OLD_TOOLS) as f:
        yield f


# ------------------------------------------------------------- pure helpers ---

def test_summary_tags_are_at_most_three():
    assert S.summary_tags({"solved": True}) == ["plate-solved"]
    assert S.summary_tags({"solved": False}, ["astro-review"]) == ["plate-solve failed", "astro-review"]
    assert len(S.summary_tags({"solved": True}, ["a", "b", "c", "d"])) == S.MAX_PLUGIN_TAGS == 3


def test_index_flags_follow_the_save_cut_and_the_group_rule():
    fr = frame()
    items = {i["label"]: i.get("index", False) for i in S.index_payload(fr["annotations"], fr, 65)["items"]}
    # Betelgeuse is covered by Orion (not taggable): a sky label, not a searchable name (same rule as tags had)
    assert items == {"Orion": True, "M42": False, "Betelgeuse": False}
    items = {i["label"]: i.get("index", False) for i in S.index_payload(fr["annotations"], fr, 40)["items"]}
    assert items == {"Orion": True, "M42": True, "Betelgeuse": False}
    assert "index" not in fr["annotations"]["items"][0]          # the result itself is not changed


def test_plugin_cap_is_the_addons_own_and_migration_removes_every_old_row():
    manual = {f"user{i}" for i in range(50)} | {"orion", "plate-solved"}
    plan = S.plan_photo(["plate-solved"], manual, set(), {"orion", "plate-solved"}, cap=3, plugin=True, migrate=True)
    assert plan["keep"] == ["plate-solved"] and plan["add"] == ["plate-solved"]
    assert plan["remove"] == ["orion", "plate-solved"]           # re-added as a summary tag
    plan = S.plan_photo(["plate-solved"], manual, set(), {"plate-solved"}, cap=3, plugin=True)
    assert plan["add"] == [] and plan["remove"] == []


# ------------------------------------------------------------- the client ---

def test_status_features_decide_model_d(new_fake, old_fake):
    f = connect(new_fake.url, token=TOKEN).features()
    assert f["model_d"] and f["plugin_tags"] and f["annotations_batch"] and f["annotation_summary"]
    f = connect(old_fake.url, token=TOKEN).features()
    assert not f["model_d"] and not f["plugin_tags"] and not f["annotations_batch"] and f["annotations_put"]


def test_one_session_per_job(new_fake):
    c = connect(new_fake.url, token=TOKEN)
    c.photo(P1)
    c.photo(P2)
    assert new_fake.mcp_inits == 2                 # outside a job: one session per call
    with c.job():
        for _ in range(5):
            c.photo(P1)
        with c.job():                              # nested: the same session
            c.photo(P2)
    assert new_fake.mcp_inits == 3
    seen = []
    t = threading.Thread(target=lambda: seen.append(c.photo(P1)))   # another thread is not inside the job
    with c.job():
        c.photo(P1)
        t.start()
        t.join()
    assert seen and new_fake.mcp_inits == 5


def test_rate_limited_is_retried_after_retry_after():
    waits = []
    c = McpFacetClient("http://127.0.0.1:9", token=TOKEN, sleep=waits.append)
    answers = iter(['{"ok": false, "error": "rate_limited", "message": "slow down", "retry_after_s": 7}',
                    '{"ok": true, "data": {"affected": 1}}'])
    c._session = lambda op: SimpleNamespace(content=[SimpleNamespace(type="text", text=next(answers))])
    assert c.call("add_tags", {"tags": ["x"], "paths": [P1]}) == {"affected": 1} and waits == [7.0]
    c._session = lambda op: SimpleNamespace(content=[SimpleNamespace(
        type="text", text='{"ok": false, "error": "rate_limited", "message": "slow down"}')])
    with pytest.raises(FacetError) as e:
        c.call("status")
    assert e.value.code == "rate_limited" and len(waits) == 1 + 3


def test_batch_writes_chunk_at_200_photos():
    c = McpFacetClient("http://127.0.0.1:9", token=TOKEN)
    sent = []

    def call(tool, args):
        sent.append(len(args["payloads"]))
        return {"results": [{"path": p, "ok": True, "indexed": 1, "index_skipped": 0} for p in args["payloads"]]}
    c.call = call
    out = c.put_annotations_batch({f"{ROOT}/{i}.jpg": {"version": 1, "items": []} for i in range(450)})
    assert sent == [200, 200, 50] and len(out) == 450 and all(r["ok"] for r in out.values())


# ------------------------------------------------------------- the writer ---

def test_model_d_save_writes_summary_tags_and_searchable_names(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    assert ctx.model_d and ctx.annotations_batch and ctx.mode == "api"
    before = new_fake.mcp_inits
    reps = S.apply_results(ctx, {P1: frame(), P2: frame(solved=False)})
    assert new_fake.mcp_inits - before == 1                     # one session for the whole save
    assert new_fake.photos[P1]["manual_tags"] == ["plate-solved"]
    assert new_fake.photos[P1]["plugin_rows"] == {"plate-solved"}
    assert new_fake.photos[P2]["manual_tags"] == ["night", "plate-solve failed"]
    assert all(a.get("plugin") is True for a in calls(new_fake, "add_tags"))
    assert not calls(new_fake, "set_annotations") and len(calls(new_fake, "set_annotations_batch")) == 1
    assert [d["label"] for d in new_fake.detections[P1]] == ["orion"]
    r1 = next(r for r in reps if r["path"] == P1)
    assert r1["annotation"] == "stored" and r1["searchable"] == 1 and r1["names"] == ["Orion"]
    row = ctx.ledger.get(ctx.library, P1)
    assert row["tags"] == ["plate-solved"] and row["tag_kind"] == "plugin"
    # a lower cut re-stores the payload; M42 becomes searchable, no tag changes
    n_add = len(calls(new_fake, "add_tags"))
    reps = S.apply_results(replace(ctx, min_confidence=40), {P1: frame()})
    assert sorted(d["label"] for d in new_fake.detections[P1]) == ["m42", "orion"]
    assert len(calls(new_fake, "add_tags")) == n_add and reps[0]["add"] == [] and reps[0]["remove"] == []
    # the same save again writes nothing
    n_batch = len(calls(new_fake, "set_annotations_batch"))
    reps = S.apply_results(replace(ctx, min_confidence=40), {P1: frame()})
    assert reps[0]["annotation"] == "unchanged" and len(calls(new_fake, "set_annotations_batch")) == n_batch


def test_migration_removes_the_old_per_object_tags_and_converges(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    p = new_fake.photos[P1]
    # what an older Facet left: astro-id's per-object tags as plain rows, one tag the user owns, one the ledger
    # lists that another writer now owns
    p["manual_tags"] = sorted(["orion", "m42", "plate-solved", "night", "jupiter"])
    p["tag_created_by"] = {"orion": "astro-id", "m42": "astro-id", "plate-solved": "astro-id", "jupiter": "someone"}
    ctx.ledger.record(ctx.library, P1, tags=["orion", "m42", "plate-solved", "jupiter"], payload_hash="old",
                      annotation_via="api", status="solved", facet_version="old")
    rep = S.apply_results(ctx, {P1: frame()})[0]
    assert rep["migrated"] == ["jupiter", "m42", "orion", "plate-solved"]
    assert sorted(p["manual_tags"]) == ["jupiter", "night", "plate-solved"]   # someone else's jupiter stays
    assert p["plugin_rows"] == {"plate-solved"} and rep["not_removed"] == ["jupiter"]
    row = ctx.ledger.get(ctx.library, P1)
    assert row["tags"] == ["plate-solved"] and row["tag_kind"] == "plugin"
    n = len(calls(new_fake, "remove_tags")), len(calls(new_fake, "add_tags"))
    rep = S.apply_results(ctx, {P1: frame()})[0]          # converged: nothing to change
    assert rep["add"] == [] and rep["remove"] == [] and "migrated" not in rep
    assert (len(calls(new_fake, "remove_tags")), len(calls(new_fake, "add_tags"))) == n


def test_old_facet_keeps_per_object_tags(old_fake, tmp_path):
    ctx = ctx_for(old_fake, tmp_path)
    assert not ctx.model_d and ctx.mode == "api"
    rep = S.apply_results(ctx, {P1: frame()})[0]
    assert old_fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    assert not any("plugin" in a for a in calls(old_fake, "add_tags"))
    assert len(calls(old_fake, "set_annotations")) == 1 and not calls(old_fake, "set_annotations_batch")
    assert all("index" not in i for i in old_fake.annotations[(P1, "astro-id")]["items"])
    assert rep["annotation"] == "stored" and ctx.ledger.get(ctx.library, P1)["tag_kind"] is None


def test_remove_all_on_model_d(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    S.apply_results(ctx, {P1: frame()})
    S.remove_all(ctx)
    assert new_fake.photos[P1]["manual_tags"] == [] and P1 not in new_fake.detections
    assert not new_fake.annotations


# ------------------------------------------------------------- the panel ---

@pytest.fixture()
def addon_d(new_fake, tmp_path):
    import http.client
    import json

    args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                          "path_map": None})()
    server, box, aenv = SV.build_addon(args, addon_env(new_fake))
    app = SV.finish_addon(args, box, aenv)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    def req(method, path, body=None):
        from test_facet_addon import SECRET

        c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        h = {"X-Facet-Launch": SECRET, "Content-Type": "application/json"}
        c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        doc = json.loads(r.read() or b"null")
        c.close()
        return r.status, doc

    yield req, app
    server.shutdown()
    server.server_close()
    app.ledger.close()


def test_panel_save_and_sky_controls_on_model_d(new_fake, addon_d, monkeypatch):
    from astro_id import session

    req, app = addon_d

    def fake_run(paths, opts, emit):
        done = {"event": "done", "solved": len(paths), "elapsed_s": 0.1, "time": {},
                "cull": {"reference": {}, "rule": {}, "frames": {p: {"decision": "keep", "reasons": []}
                                                                  for p in paths}}}
        emit(done)
        return {**done, "results": {p: frame() for p in paths}}
    monkeypatch.setattr(session, "run_session", fake_run)
    st, state = req("GET", "/ui/api/state")
    assert st == 200 and state["names_as"] == "searchable"
    req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7", "category": "astro"})
    st, r = req("POST", "/ui/api/solve", {"paths": [P1], "tolerance_min": "5"})
    assert st == 200
    for _ in range(200):
        _, j = req("GET", "/ui/api/job?after=0")
        if not j["running"]:
            break
        time.sleep(0.02)
    st, s = req("POST", "/ui/api/save", {"min_confidence": 65})
    assert st == 200 and s["names_as"] == "searchable" and s["searchable"] == 1 and s["sky_labels"] == 1
    assert new_fake.photos[P1]["manual_tags"] == ["plate-solved"]
    st, a = req("GET", "/ui/api/annotations?path=" + P1)
    assert st == 200 and a["items"] == 3 and a["searchable"] == 1
    assert [g["id"] for g in a["payload"]["groups"]] == ["constellation", "catalog", "star"]
    assert calls(new_fake, "get_annotations")[-1]["summary"] is True


# ------------------------------------------------- review findings 1-4, 6, 7 ---

def _as_mcp_status_failing(client, state):
    """Make the client's ``status`` tool fail while ``state['down']`` is true."""
    real = client.call

    def call(tool, args=None):
        if tool == "status" and state["down"]:
            raise FacetError("status: rate_limited: slow down", 429, code="rate_limited")
        return real(tool, args)
    client.call = call


def test_f1_model_d_without_annotations_keeps_per_object_tags(new_fake, tmp_path):
    """Sky labels off: names cannot be stored as searchable detections, so no summary mode and no tag removal."""
    ctx = ctx_for(new_fake, tmp_path, annotations=False)
    assert not ctx.model_d and ctx.mode is None
    p = new_fake.photos[P1]
    p["manual_tags"] = sorted(["orion", "plate-solved"])
    p["tag_created_by"] = {"orion": "astro-id", "plate-solved": "astro-id"}
    ctx.ledger.record(ctx.library, P1, tags=["orion", "plate-solved"], payload_hash=None, annotation_via=None,
                      status="solved", facet_version="x")
    S.apply_results(ctx, {P1: frame()})
    assert p["manual_tags"] == ["orion", "plate-solved"]
    assert not calls(new_fake, "remove_tags") and not any("plugin" in a for a in calls(new_fake, "add_tags"))
    assert any("per-object" in w for w in ctx.warnings)


def test_f1_model_d_token_without_annotation_write_keeps_per_object_tags(tmp_path):
    tools = set(ASTRO_TOOLS)              # no annotations.write: no set_annotations
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=tools, model_d=True) as fake:
        ctx = ctx_for(fake, tmp_path)
        assert not ctx.model_d
        S.apply_results(ctx, {P1: frame()})
        assert fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
        assert not any("plugin" in a for a in calls(fake, "add_tags"))


def test_f2_a_failure_mid_save_still_records_the_ledger(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    real = ctx.client.batch_add_tag

    def add_then_die(tag, paths, plugin=False):
        real(tag, paths, plugin=plugin)
        raise ConnectionError("Facet restarted")
    ctx.client.batch_add_tag = add_then_die
    with pytest.raises(ConnectionError):
        S.apply_results(ctx, {P1: frame()})
    assert new_fake.photos[P1]["manual_tags"] == ["plate-solved"]
    row = ctx.ledger.get(ctx.library, P1)
    assert row and row["tags"] == ["plate-solved"] and row["tag_kind"] == "plugin"


def test_f2_model_d_adopts_summary_tags_facet_says_are_ours(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    S.apply_results(ctx, {P2: frame(solved=False)})
    assert "plate-solve failed" in new_fake.photos[P2]["manual_tags"]
    ctx.ledger.forget(ctx.library, P2)               # the record of that save was lost
    S.apply_results(ctx, {P2: frame()})
    assert new_fake.photos[P2]["manual_tags"] == ["night", "plate-solved"]
    assert ctx.ledger.get(ctx.library, P2)["tags"] == ["plate-solved"]


def _seed_cli_tags(fake, ctx, path=P1, tags=("orion", "m42")):
    p = fake.photos[path]
    p["manual_tags"] = sorted(set(p["manual_tags"]) | set(tags) | {"plate-solved"})
    p["tag_created_by"] = {t: "user" for t in tags} | {"plate-solved": "astro-id"}
    ctx.ledger.record(ctx.library, path, tags=list(tags) + ["plate-solved"], payload_hash="old",
                      annotation_via="api", status="solved", facet_version="old")


def test_f3_user_path_tags_stay_in_the_ledger_as_legacy(new_fake, tmp_path):
    ctx = ctx_for(new_fake, tmp_path)
    _seed_cli_tags(new_fake, ctx)
    rep = S.apply_results(ctx, {P1: frame()})[0]
    assert rep["legacy"] == ["m42", "orion"] and rep["not_removed"] == ["m42", "orion"]
    row = ctx.ledger.get(ctx.library, P1)
    assert row["legacy_tags"] == ["m42", "orion"] and row["tags"] == ["plate-solved"]
    assert "orion" in new_fake.photos[P1]["manual_tags"]       # the user's rows are untouched
    rep = S.apply_results(ctx, {P1: frame()})[0]               # a second save still reports them
    assert rep["legacy"] == ["m42", "orion"]
    new_fake.photos[P1]["manual_tags"].remove("m42")           # the user deleted one: no longer listed
    assert S.apply_results(ctx, {P1: frame()})[0]["legacy"] == ["orion"]


def test_f3_panel_save_reports_old_tags_that_are_the_users(new_fake, addon_d, monkeypatch):
    from astro_id import session

    req, app = addon_d
    _seed_cli_tags(new_fake, app.ctx)

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
        if not req("GET", "/ui/api/job?after=0")[1]["running"]:
            break
        time.sleep(0.02)
    st, s = req("POST", "/ui/api/save", {"min_confidence": 65})
    assert st == 200 and s["old_tags_yours"] == 2


def test_f4_failed_status_is_not_remembered(new_fake):
    c = connect(new_fake.url, token=TOKEN)
    c.status_retry_s = 0
    state = {"down": True}
    _as_mcp_status_failing(c, state)
    assert c.status() == {}
    state["down"] = False
    assert c.status().get("server_version") and c.features()["model_d"]


def test_f4_model_d_is_read_from_tools_list_when_status_fails(new_fake):
    new_fake.mcp_tools = set(NEW_TOOLS) - {"status"}        # status refused: forbidden_level
    f = connect(new_fake.url, token=TOKEN).features()
    assert f["model_d"] and f["plugin_tags"] and f["annotations_batch"] and "uncertain" not in f


def test_f4_status_down_at_start_is_asked_again_before_the_first_save(new_fake, tmp_path):
    client = connect(new_fake.url, token=TOKEN)
    client.status_retry_s = 0
    state = {"down": True}
    _as_mcp_status_failing(client, state)
    real_tools = client.tools

    def tools_without_plugin():      # tools/list shows no `plugin` parameter: status was the only evidence
        out = real_tools()
        client._tool_defs["add_tags"] = {"properties": {"tags": {}, "paths": {}}}
        return out
    client.tools = tools_without_plugin
    ctx = S.SyncContext(client=client, ledger=Ledger(tmp_path / "l.sqlite"), addon=True).prepare()
    assert not ctx.model_d and ctx.uncertain
    state["down"] = False
    S.apply_results(ctx, {P1: frame()})
    assert ctx.model_d and not ctx.uncertain
    assert new_fake.photos[P1]["plugin_rows"] == {"plate-solved"}


def test_f6_old_facet_ignores_plugin_and_the_writer_never_sends_it(old_fake, tmp_path):
    c = connect(old_fake.url, token=TOKEN)
    assert not c.features()["model_d"] and "plugin" not in c._tool_defs["add_tags"].get("properties", {})
    c.batch_add_tag("zz", [P1], plugin=True)             # what a misdetection would do: a silent plain row
    assert "zz" in old_fake.photos[P1]["manual_tags"] and not old_fake.photos[P1]["plugin_rows"]
    ctx = ctx_for(old_fake, tmp_path)
    S.apply_results(ctx, {P1: frame(), P2: frame(solved=False)})
    sent = [a for a in calls(old_fake, "add_tags") if a["tags"] != ["zz"]]
    assert sent and not any("plugin" in a for a in sent)


def test_f7_failed_later_chunk_keeps_the_stored_ones(new_fake, tmp_path, monkeypatch):
    from astro_id.facet import mcp_client

    monkeypatch.setattr(mcp_client, "ANN_BATCH", 1)
    ctx = ctx_for(new_fake, tmp_path)
    real = ctx.client.call
    n = {"batch": 0}

    def call(tool, args=None):
        if tool == "set_annotations_batch":
            n["batch"] += 1
            if n["batch"] == 2:
                raise FacetError("set_annotations_batch: rate_limited: slow down", 429, code="rate_limited")
        return real(tool, args)
    ctx.client.call = call
    reps = {r["path"]: r for r in S.apply_results(ctx, {P1: frame(), P2: frame()})}
    assert reps[P1]["annotation"] == "stored" and reps[P2]["annotation"].startswith("error")
    assert ctx.ledger.get(ctx.library, P1)["payload_hash"] and not ctx.ledger.get(ctx.library, P2)["payload_hash"]
    again = S.apply_results(ctx, {P1: frame(), P2: frame()})
    assert {r["path"]: r["annotation"] for r in again} == {P1: "unchanged", P2: "stored"}


def test_f7_put_annotations_batch_reports_per_chunk():
    c = McpFacetClient("http://127.0.0.1:9", token=TOKEN)
    sent = []

    def call(tool, args):
        sent.append(len(args["payloads"]))
        if len(sent) == 3:
            raise FacetError("set_annotations_batch: rate_limited: slow down", 429, code="rate_limited")
        return {"results": [{"path": p, "ok": True, "indexed": 1, "index_skipped": 0} for p in args["payloads"]]}
    c.call = call
    out = c.put_annotations_batch({f"{ROOT}/{i}.jpg": {"version": 1, "items": []} for i in range(450)})
    ok = [p for p, r in out.items() if r["ok"]]
    assert sent == [200, 200, 50] and len(ok) == 400 and len(out) == 450
    assert all("rate_limited" in r["message"] for r in out.values() if not r["ok"])


def test_f8_review_rows_count_the_sky_label_items_that_can_be_indexed():
    fr = frame()
    fr["annotations"]["items"].append({"id": "x", "label": "has, comma", "group": "star", "score": 99, "shape": "point"})
    fr["objects"].append({"name": "has, comma", "tag": "has, comma", "confidence": 99, "level": 5, "taggable": True})
    fr["objects"].append({"name": "Faint", "tag": "faint", "confidence": 99, "level": 5, "taggable": True})
    rows = SV.review_rows([P1], {P1: fr})
    items = {o["name"]: o["items"] for o in rows[0]["objects"]}
    assert items == {"Orion": 1, "M42": 1, "Betelgeuse": 1, "has, comma": 0, "Faint": 0}
