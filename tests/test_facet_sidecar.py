"""Facet sidecar: ledger, tag planning, the HTTP client and the sync / serve layers against a fake Facet."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from astro_id.facet import sync as S
from astro_id.facet.client import ALL_FRAMES, FacetClient, FacetError
from astro_id.facet.ledger import Ledger, data_dir, default_ledger_path, library_key
from fake_facet import FakeFacet

ROOT = "//nas/Photos/night"
P1, P2, P3 = f"{ROOT}/A1.JPG", f"{ROOT}/A2.JPG", f"{ROOT}/A3.JPG"


def photos():
    return [
        {"path": P1, "filename": "A1.JPG", "category": "astro", "date_taken": "2019:11:05 09:31:59",
         "gps_latitude": 32.5, "gps_longitude": -116.25, "gps_manually_set": 0, "raw_companion": "A1.ARW"},
        {"path": P2, "filename": "A2.JPG", "category": "astro", "date_taken": "2019:11:05 09:40:00",
         "manual_tags": ["my trip"], "tags": "night sky,stars", "date_manually_set": 1, "raw_companion": ""},
        {"path": P3, "filename": "A3.JPG", "category": "landscape", "date_taken": "2019:11:06 01:00:00"},
    ]


def frame(solved=True, objs=(), ann=True, quality=None):
    fr = {"solved": solved, "objects": list(objs), "quality": quality}
    if solved and ann:
        fr["annotations"] = {"version": 1, "source": "astro-id", "image": {"width": 10, "height": 10},
                             "items": [{"id": "x", "label": "X", "group": "planet", "score": 90, "shape": "point",
                                        "x": 1, "y": 2}]}
    return fr


def obj(tag, conf=90, level=1, taggable=True):
    return {"tag": tag, "confidence": conf, "level": level, "taggable": taggable}


@pytest.fixture()
def ledger(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite") as led:
        yield led


@pytest.fixture()
def fake():
    with FakeFacet(photos()) as f:
        yield f


def ctx_for(fake, ledger, **kw):
    c = FacetClient(fake.url, edition_password="pw")
    return S.SyncContext(client=c, ledger=ledger, **kw).prepare()


# ------------------------------------------------------------------ ledger ---

def test_ledger_record_get_meta_and_library_separation(ledger):
    a, b = library_key("http://127.0.0.1:5001/"), library_key("HTTP://127.0.0.1:5003")
    assert a == "http://127.0.0.1:5001" and b == "http://127.0.0.1:5003"
    ledger.record(a, P1, tags=["orion", "plate-solved", "orion"], payload_hash="h", annotation_via="api",
                  status="solved", facet_version="1.29.0")
    r = ledger.get(a, P1)
    assert r["tags"] == ["orion", "plate-solved"] and r["payload_hash"] == "h" and r["annotation_via"] == "api"
    assert ledger.get(b, P1) is None and ledger.known_paths(a) == {P1} and ledger.known_paths(b) == set()
    ledger.set_meta(a, "since_watermark", "2019-11-05")
    assert ledger.get_meta(a, "since_watermark") == "2019-11-05" and ledger.get_meta(b, "since_watermark") is None
    ledger.forget(a, P1)
    assert ledger.rows(a) == []


def test_ledger_is_usable_from_other_threads(ledger):
    errs = []

    def work(i):
        try:
            ledger.record("lib", f"p{i}", tags=[str(i)], payload_hash=None, annotation_via=None, status="solved",
                          facet_version=None)
        except Exception as exc:  # pragma: no cover
            errs.append(exc)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(ledger.known_paths("lib")) == 8


def test_default_ledger_location(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRO_ID_LEDGER", str(tmp_path / "x.sqlite"))
    assert default_ledger_path() == tmp_path / "x.sqlite"
    monkeypatch.delenv("ASTRO_ID_LEDGER")
    assert default_ledger_path().name == "facet-ledger.sqlite" and default_ledger_path().parent == data_dir()
    assert data_dir().name == "astro-id"


# ---------------------------------------------------------------- planning ---

def test_normalize_tag_matches_facet_rules():
    assert S.normalize_tag("  M45   Pleiades ") == "m45 pleiades"
    assert S.normalize_tag("a,b") is None and S.normalize_tag("") is None and S.normalize_tag("x" * 65) is None
    assert S.normalize_tag("zero\u200bwidth") is None and S.normalize_tag(None) is None


def test_desired_tags_order_status_extra_and_filters():
    fr = frame(objs=[obj("orion's belt", 95, 4), obj("jupiter", 99, 2), obj("orion", 80, 1), obj("taurus", 70, 1),
                     obj("faint", 50, 1), obj("member", 99, 5, taggable=False)])
    assert S.desired_tags(fr, 65, ["astro-review"]) == ["plate-solved", "astro-review", "orion", "taurus",
                                                        "jupiter", "orion's belt"]
    assert S.desired_tags(frame(solved=False, objs=[obj("x")]), 65) == ["plate-solve failed"]


def test_plan_keeps_foreign_skips_ai_and_removes_stale_own():
    p = S.plan_photo(["plate-solved", "orion", "stars", "my trip"], manual={"my trip", "old", "plate-solved"},
                     ai={"stars"}, mine={"old", "plate-solved", "gone-by-user"})
    assert p["keep"] == ["plate-solved", "orion"] and p["add"] == ["orion"]
    assert p["remove"] == ["old"]                 # 'gone-by-user' is not in Facet any more: nothing to remove
    assert p["skipped"] == ["stars", "my trip"]   # AI tag; a user tag is never claimed


def test_plan_cap_counts_foreign_first_and_drops_lowest_priority():
    foreign = {f"u{i}" for i in range(48)}
    p = S.plan_photo(["plate-solved", "uranus", "orion", "taurus"], manual=foreign, ai=set(), mine=set())
    assert p["keep"] == ["plate-solved", "uranus"] and p["dropped"] == ["orion", "taurus"]


def test_hints_gps_source_and_dates():
    assert S.gps_source({"gps_latitude": 1.0, "gps_longitude": 2.0}) == "facet-exif"
    assert S.gps_source({"gps_latitude": 1.0, "gps_longitude": 2.0, "gps_manually_set": 1}) == "facet-manual"
    assert S.gps_source({"gps_latitude": 0, "gps_longitude": 0}) == "none" and S.gps_source({}) == "none"
    h = S.facet_hints({"gps_latitude": 32.5, "gps_longitude": -116.25, "date_taken": "2026:11:07 20:34:00"})
    assert h == {"site": "32.500000,-116.250000", "time": None}          # camera clock: not a true time
    h = S.facet_hints({"date_taken": "2026:11:07 20:34:00", "date_manually_set": 1})
    assert h == {"site": None, "time": "2026-11-07T20:34:00"}
    assert S.parse_facet_date("2026-11-07T20:34:00") is not None and S.parse_facet_date("garbage") is None


def test_path_map():
    pairs = S.parse_path_map(["/photos=//nas/Photos"])
    assert S.to_local("/photos/2026/a.jpg", pairs) == "//nas/Photos/2026/a.jpg"
    assert S.to_local("/other/a.jpg", pairs) == "/other/a.jpg"
    with pytest.raises(ValueError):
        S.parse_path_map(["nonsense"])


def test_annotation_payload_rules():
    assert S.annotation_payload(frame(solved=False)) is None
    assert S.annotation_payload(frame(ann=False)) is S._UNSET
    p = S.annotation_payload(frame(quality={"grade": "A"}))
    assert p["quality"] == {"grade": "A"} and p["version"] == 1
    assert S.payload_hash(p) == S.payload_hash(json.loads(json.dumps(p)))


# ------------------------------------------------------------------ client ---

def test_client_login_paging_and_all_frames(fake):
    c = FacetClient(fake.url, edition_password="pw")
    got = c.list_photos(path_prefix=ROOT + "\\", category="astro")
    assert [p["path"] for p in got] == [P1, P2]
    q = [x for x in fake.calls if x[1] == "/api/photos"][0][2]
    assert all(q[k] == v for k, v in ALL_FRAMES.items()) and q["per_page"] == "500"
    assert c.server_version() == "1.29.0"
    assert c.photo("//nope") is None


def test_client_relogin_after_expired_token_and_bad_password(fake):
    c = FacetClient(fake.url, edition_password="pw")
    c.batch_add_tag("x", [P1])
    fake.expire_next = True
    c.batch_add_tag("y", [P1])                    # 401 -> login again -> retried
    assert {"x", "y"} <= set(fake.photos[P1]["manual_tags"]) and fake.logins == 2
    with pytest.raises(FacetError) as ei:
        FacetClient(fake.url, edition_password="wrong").login()
    assert ei.value.status == 401


def test_client_anonymous_writes_are_refused(fake):
    c = FacetClient(fake.url)
    assert not c.can_login and c.login() is None
    with pytest.raises(FacetError) as ei:
        c.batch_add_tag("x", [P1])
    assert ei.value.status == 401


def test_feature_detection(fake):
    assert FacetClient(fake.url, edition_password="pw").features() == {"annotations_put": True,
                                                                       "annotations_read": True,
                                                                       "plugin_tags": False, "model_d": False}
    fake.annotations_put = False
    assert FacetClient(fake.url, edition_password="pw").features()["annotations_put"] is False


def test_client_from_env(monkeypatch):
    monkeypatch.setenv("FACET_URL", "http://127.0.0.1:9/")
    monkeypatch.setenv("FACET_EDITION_PASSWORD", "s3cret")
    monkeypatch.delenv("FACET_TOKEN", raising=False)
    c = FacetClient.from_env()
    assert c.url == "http://127.0.0.1:9" and c.edition_password == "s3cret" and c.can_login


# -------------------------------------------------------------------- sync ---

def test_apply_writes_then_rerun_is_a_no_op(fake, ledger):
    ctx = ctx_for(fake, ledger)
    res = {P1: frame(objs=[obj("orion"), obj("jupiter", level=2)]), P2: frame(objs=[obj("orion"), obj("stars")])}
    rep = S.apply_results(ctx, res)
    assert fake.photos[P1]["manual_tags"] == ["jupiter", "orion", "plate-solved"]
    assert fake.photos[P2]["manual_tags"] == ["my trip", "orion", "plate-solved"]   # 'stars' is an AI tag
    assert {r["annotation"] for r in rep} == {"stored"} and len(fake.annotations) == 2
    assert ledger.tags(ctx.library, P2) == ["orion", "plate-solved"]
    n = len(fake.writes())
    rep2 = S.apply_results(ctx, res)
    assert len(fake.writes()) == n                                  # nothing written the second time
    assert all(not r["add"] and not r["remove"] and r["annotation"] == "unchanged" for r in rep2)


def test_rerun_removes_only_own_stale_tags_and_unsolved_drops_annotation(fake, ledger):
    ctx = ctx_for(fake, ledger)
    S.apply_results(ctx, {P1: frame(objs=[obj("orion"), obj("taurus")])})
    fake.photos[P1]["manual_tags"].append("taurus-user")            # a user tag added meanwhile
    S.apply_results(ctx, {P1: frame(solved=False)})
    assert fake.photos[P1]["manual_tags"] == ["plate-solve failed", "taurus-user"]
    assert fake.annotations == {} and ledger.get(ctx.library, P1)["payload_hash"] is None


def test_user_owned_tag_is_never_claimed_or_removed(fake, ledger):
    fake.photos[P1]["manual_tags"] = ["orion"]                      # the user typed it first
    ctx = ctx_for(fake, ledger)
    S.apply_results(ctx, {P1: frame(objs=[obj("orion")])})
    assert ledger.tags(ctx.library, P1) == ["plate-solved"]
    S.apply_results(ctx, {P1: frame(solved=False)})
    assert "orion" in fake.photos[P1]["manual_tags"]


def test_dry_run_writes_nothing(fake, ledger):
    ctx = ctx_for(fake, ledger, dry_run=True)
    rep = S.apply_results(ctx, {P1: frame(objs=[obj("orion")])})
    assert rep[0]["add"] == ["plate-solved", "orion"] and rep[0]["annotation"] == "would store"
    assert fake.writes() == [] and ledger.rows(ctx.library) == []


def test_no_annotation_route_skips_with_warning(fake, ledger):
    fake.annotations_put = False
    ctx = ctx_for(fake, ledger)
    assert ctx.mode is None and "annotations skipped" in ctx.warnings[0]
    rep = S.apply_results(ctx, {P1: frame(objs=[obj("orion")])})
    assert rep[0]["annotation"].startswith("skipped") and fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]


LEGACY_MODULE = '''
import json
def upsert_annotation(conn, photo_path, source, payload):
    if conn.execute("SELECT 1 FROM photos WHERE path = ?", (photo_path,)).fetchone() is None:
        raise LookupError(photo_path)
    conn.execute("INSERT OR REPLACE INTO photo_annotations VALUES (?, ?, ?)", (photo_path, source, json.dumps(payload)))
def delete_annotation(conn, photo_path, source):
    return conn.execute("DELETE FROM photo_annotations WHERE photo_path = ? AND source = ?",
                        (photo_path, source)).rowcount > 0
'''


def test_legacy_db_fallback_only_when_route_missing(fake, ledger, tmp_path):
    import sqlite3

    fake.annotations_put = False
    (tmp_path / "facet" / "db").mkdir(parents=True)
    (tmp_path / "facet" / "db" / "annotations.py").write_text(LEGACY_MODULE, encoding="utf-8")
    db = tmp_path / "lib.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE photos (path TEXT PRIMARY KEY)")
        c.execute("CREATE TABLE photo_annotations (photo_path TEXT, source TEXT, payload TEXT, "
                  "PRIMARY KEY (photo_path, source))")
        c.execute("INSERT INTO photos VALUES (?)", (P1,))
    ctx = ctx_for(fake, ledger, legacy_db=str(db), facet_dir=str(tmp_path / "facet"))
    assert ctx.mode == "legacy-db" and "TEMPORARY" in ctx.warnings[0]
    rep = S.apply_results(ctx, {P1: frame(objs=[obj("orion")])})
    assert rep[0]["annotation"] == "stored"
    S.apply_results(ctx, {P1: frame(solved=False)})
    ctx.close()
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM photo_annotations").fetchone()[0] == 0
    fake.annotations_put = True                                     # the route exists: the db is never opened
    assert ctx_for(fake, ledger, legacy_db=str(db), facet_dir="/nowhere").mode == "api"


def test_legacy_db_needs_facet_dir(fake, ledger, tmp_path, monkeypatch):
    fake.annotations_put = False
    monkeypatch.delenv("FACET_DIR", raising=False)
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    with pytest.raises(ValueError, match="facet-dir"):
        ctx_for(fake, ledger, legacy_db=str(db))


def test_undo_removes_ledger_tags_and_annotations(fake, ledger):
    ctx = ctx_for(fake, ledger)
    S.apply_results(ctx, {P1: frame(objs=[obj("orion")]), P2: frame(objs=[obj("orion")])})
    S.remove_all(ctx)
    assert fake.photos[P1]["manual_tags"] == [] and fake.photos[P2]["manual_tags"] == ["my trip"]
    assert fake.annotations == {} and ledger.rows(ctx.library) == []


def test_select_candidates_pending_force_since_and_spool(fake, ledger, tmp_path):
    ctx = ctx_for(fake, ledger)
    sel = S.Selection(path_prefix=ROOT)
    picked, info = S.select_candidates(ctx.client, ledger, ctx.library, sel)
    assert [p["path"] for p in picked] == [P1, P2] and info["watermark"] == "2019-11-05"
    S.apply_results(ctx, {P1: frame()})
    picked, info = S.select_candidates(ctx.client, ledger, ctx.library, sel)
    assert [p["path"] for p in picked] == [P2] and info["already_synced"] == 1
    picked, _ = S.select_candidates(ctx.client, ledger, ctx.library, S.Selection(path_prefix=ROOT, force=True))
    assert len(picked) == 2
    spool = tmp_path / "spool.txt"
    spool.write_text(f"{P3}\n{P3}\n", encoding="utf-8")
    picked, _ = S.select_candidates(ctx.client, ledger, ctx.library, S.Selection(path_prefix=ROOT, spool=str(spool)))
    assert [p["path"] for p in picked] == [P2, P3]
    ledger.set_meta(ctx.library, S.WATERMARK, "2019-11-06")
    S.select_candidates(ctx.client, ledger, ctx.library, S.Selection(since="last", category=""))
    assert [x for x in fake.calls if x[1] == "/api/photos"][-1][2]["date_from"] == "2019-11-06"


def test_solve_and_write_passes_facet_hints_and_path_map(fake, ledger):
    from astro_id.session import SessionOptions

    ctx = ctx_for(fake, ledger)
    seen = {}

    def fake_run(paths, opts, emit):
        seen["paths"], seen["hints"] = paths, opts.hints
        return {"event": "done", "solved": len(paths), "results": {p: frame(objs=[obj("orion")]) for p in paths}}
    out = S.solve_and_write(ctx, [fake.photos[P1], fake.photos[P2]], SessionOptions(), path_map=[(ROOT, "Z:/night")],
                            run_session_fn=fake_run)
    assert seen["paths"] == ["Z:/night/A1.JPG", "Z:/night/A2.JPG"]
    assert seen["hints"]["Z:/night/A1.JPG"] == {"site": "32.500000,-116.250000", "time": None}
    assert seen["hints"]["Z:/night/A2.JPG"]["time"] == "2019-11-05T09:40:00"
    assert set(out["results"]) == {P1, P2} and len(out["reports"]) == 2
    assert "orion" in fake.photos[P1]["manual_tags"]


def test_cli_sync_dry_run_end_to_end(fake, tmp_path, monkeypatch, capsys):
    from astro_id import cli, session

    monkeypatch.setenv("FACET_EDITION_PASSWORD", "pw")
    monkeypatch.setattr(session, "run_session",
                        lambda paths, opts, emit: {"event": "done", "results": {p: frame(objs=[obj("orion")])
                                                                                for p in paths}})
    rc = cli.main(["facet", "sync", "--facet-url", fake.url, "--ledger", str(tmp_path / "l.sqlite"),
                   "--path-prefix", ROOT, "--dry-run", "--json"])
    assert rc == 0 and fake.writes() == []
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[0]["event"] == "sync_start" and lines[0]["to_solve"] == 2 and lines[-1]["tags_added"] == 4
    rc = cli.main(["facet", "sync", "--facet-url", fake.url, "--ledger", str(tmp_path / "l.sqlite"),
                   "--path-prefix", ROOT])
    assert rc == 0 and fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    rc = cli.main(["facet", "sync", "--facet-url", fake.url, "--ledger", str(tmp_path / "l.sqlite"),
                   "--path-prefix", ROOT, "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out.splitlines()[-1])["solved"] == 0   # nothing pending


# ------------------------------------------------------------------- serve ---

@pytest.fixture()
def ui(fake, ledger):
    from astro_id.facet import serve as SV

    server, url, app = SV.build(type("A", (), {"port": 0})(), client=FacetClient(fake.url, edition_password="pw"),
                                ledger=ledger, secret="s3cr3t")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield url, app
    server.shutdown()
    server.server_close()


def _req(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h)) as r:
        ct = r.headers.get("Content-Type")
        raw = r.read()
    return json.loads(raw) if "json" in ct else raw.decode()


def test_serve_needs_secret_and_loopback_host(ui):
    url, _ = ui
    base = url.split("/s3cr3t/")[0]
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(base + "/api/state")
    assert ei.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(url + "api/state", headers={"Host": "evil.example"})
    assert ei.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(url + "api/photos", {}, headers={"Origin": "http://evil.example"})
    assert ei.value.code == 403
    assert "Astro session" in _req(url)


def test_serve_flow_load_gps_solve_recull_review_tag(ui, fake, monkeypatch):
    from astro_id import session

    url, app = ui
    st = _req(url + "api/state")
    assert st["annotations"] == "api" and st["can_write"] and st["version"] == "1.29.0"
    d = _req(url + "api/photos", {"path_prefix": ROOT, "category": "astro"})
    assert [p["gps_source"] for p in d["photos"]] == ["facet-exif", "none"] and d["raw_companion_field"]
    assert d["photos"][0]["facet_link"].startswith(fake.url + "/photo?path=")
    # R-LOC: a location is set for the whole folder (two photos there have no GPS), after a check and one confirm
    app.elevation = lambda lat, lon: None
    loc = {"scope": {"folder": ROOT}, "text": "https://www.google.com/maps/@32.61,-116.32,15z"}
    g = _req(url + "api/location", loc)
    assert (g["lat"], g["lon"], g["changed"], g["skipped"], g["confirmed"]) == (32.61, -116.32, 2, 1, False)
    assert fake.photos[P2].get("gps_latitude") is None
    g = _req(url + "api/location", {**loc, "confirm": True})
    assert g["confirmed"] and fake.photos[P2]["gps_latitude"] == 32.61 and fake.photos[P2]["gps_manually_set"] == 1
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(url + "api/location", {"scope": {"folder": ROOT}, "text": "no coordinates"})
    assert ei.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(url + "api/gps", {"path": P2, "text": "32.6, -116.3"})                 # no per-photo route any more
    assert ei.value.code == 404

    q = {"detected": 900, "fwhm_px": 5.0, "elongation": 1.1}
    bad = {"detected": 10, "fwhm_px": 30.0, "elongation": 1.1}

    def fake_run(paths, opts, emit):
        assert opts.tolerance_min == 5 and opts.hints[P2]["site"] == "32.610000,-116.320000"
        emit({"event": "start", "mode": "solve", "total": len(paths)})
        assert opts.min_confidence == 85
        res = {paths[0]: frame(objs=[obj("orion"), obj("weak", 80)], quality=q),
               paths[1]: frame(objs=[obj("orion")], quality=bad)}
        from astro_id.cull import cull_session
        c = cull_session({p: r["quality"] for p, r in res.items()}, opts.cull, {p: True for p in res})
        done = {"event": "done", "solved": 2, "elapsed_s": 0.1, "time": {},
                "cull": {"reference": c["reference"], "rule": c["rule"],
                         "frames": {p: {"decision": v["decision"], "reasons": v["reasons"]} for p, v in c["frames"].items()}}}
        emit(done)
        return {**done, "results": res}
    monkeypatch.setattr(session, "run_session", fake_run)
    r = _req(url + "api/solve", {"paths": [P1, P2], "tolerance_min": "5", "write": True, "min_confidence": 85})
    assert r["started"] == 2
    for _ in range(100):
        j = _req(url + "api/job?after=0")
        if not j["running"]:
            break
        threading.Event().wait(0.05)
    assert j["error"] is None and j["done"]["cull"]["frames"][P2]["decision"] == "reject"
    # Solving writes nothing to Facet: the user reviews first, then saves.
    assert not any(e["event"] == "written" for e in j["events"]) and not j["saved"]
    assert "orion" not in fake.photos[P2]["manual_tags"] and not fake.annotations
    rv = {r["path"]: r for r in j["done"]["review"]}
    assert rv[P1]["status"] == "solved" and [o["name"] for o in rv[P1]["objects"]] == ["orion", "weak"]
    assert rv[P1]["objects"][0]["confidence"] == 90 and rv[P1]["objects"][0]["group"] == "constellation"
    n0 = len(fake.writes())
    with pytest.raises(urllib.error.HTTPError) as ei:              # culling's tag waits for a Save
        _req(url + "api/review-tags", {"paths": [P2]})
    assert ei.value.code == 400 and len(fake.writes()) == n0
    s1 = _req(url + "api/save", {})
    assert s1["photos"] == 2 and s1["errors"] == 0 and s1["sky_labels"] == 2 and s1["annotations"] == "api"
    assert "orion" in fake.photos[P2]["manual_tags"] and len(fake.annotations) == 2
    assert "weak" not in fake.photos[P1]["manual_tags"]       # regression: the session's min confidence (85) is the tag cut
    assert _req(url + "api/job?after=0")["saved"] is True
    n = len(fake.writes())
    s2 = _req(url + "api/save", {})                           # saving again: no new writes (ledger)
    assert len(fake.writes()) == n and s2["tags_added"] == 0 and s2["tags_removed"] == 0
    # Step 5's own slider: a lower save cut, ticked photo P1 only -> "weak" (80) becomes a tag there, P2 untouched.
    s3 = _req(url + "api/save", {"min_confidence": 75, "paths": [P1]})
    assert s3["photos"] == 1 and s3["tags_added"] == 1 and "weak" in fake.photos[P1]["manual_tags"]
    s4 = _req(url + "api/save", {"min_confidence": 85, "paths": [P1]})   # raised again: removed again
    assert s4["tags_removed"] == 1 and "weak" not in fake.photos[P1]["manual_tags"]
    rc = _req(url + "api/recull", {"rule": {"enabled": True, "order": ["focus"], "thresholds": {}}})
    assert rc["frames"][P2]["decision"] == "review"          # defocus alone never rejects
    with pytest.raises(urllib.error.HTTPError):
        _req(url + "api/recull", {"rule": {"order": ["nonsense"]}})
    _req(url + "api/review-tags", {"paths": [P2]})
    assert "astro-review" in fake.photos[P2]["manual_tags"] and "astro-review" not in fake.photos[P1]["manual_tags"]
    _req(url + "api/review-tags", {"paths": []})
    assert "astro-review" not in fake.photos[P2]["manual_tags"] and "orion" in fake.photos[P2]["manual_tags"]
    assert not any(c[1] in ("/api/photos/batch_reject", "/api/photos/batch_review") for c in fake.calls)


def _run_job(url, monkeypatch, res_for):
    from astro_id import session

    seen = {}

    def fake_run(paths, opts, emit):
        seen["opts"] = opts
        done = {"event": "done", "solved": len(paths), "elapsed_s": 0.1, "time": {},
                "cull": {"reference": {}, "rule": {}, "frames": {p: {"decision": "keep", "reasons": []} for p in paths}}}
        emit(done)
        return {**done, "results": {p: res_for(p) for p in paths}}
    monkeypatch.setattr(session, "run_session", fake_run)
    return seen


def _wait(url):
    for _ in range(100):
        j = _req(url + "api/job?after=0")
        if not j["running"]:
            return j
        threading.Event().wait(0.05)
    raise AssertionError("solve did not finish")


def test_solve_does_not_write_and_save_writes_once(ui, fake, monkeypatch):
    url, app = ui
    _req(url + "api/photos", {"path_prefix": ROOT, "category": "astro"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(url + "api/save", {})                                # nothing solved yet
    assert ei.value.code == 400
    seen = _run_job(url, monkeypatch, lambda p: frame(objs=[obj("orion")]))
    _req(url + "api/solve", {"paths": [P1, P2], "write": True})   # "write" is ignored now
    j = _wait(url)
    assert seen["opts"].no_overlay is True                        # labeled copies are off by default
    assert fake.writes() == [] and not j["reports"]
    r = _req(url + "api/save", {"paths": [P1]})                   # a subset
    assert r["photos"] == 1 and fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    assert "orion" not in fake.photos[P2]["manual_tags"]
    _req(url + "api/save", {})
    n = len(fake.writes())
    assert _req(url + "api/save", {})["tags_added"] == 0 and len(fake.writes()) == n
    with pytest.raises(urllib.error.HTTPError):
        _req(url + "api/save", {"paths": ["//nas/other.jpg"]})


def test_labeled_copies_option_and_old_overlay_key(ui, fake, monkeypatch):
    url, app = ui
    _req(url + "api/photos", {"path_prefix": ROOT})
    seen = _run_job(url, monkeypatch, lambda p: frame())
    _req(url + "api/solve", {"paths": [P1], "labeled_copies": {"on": True, "lines": False, "scores": True}})
    _wait(url)
    o = seen["opts"]
    assert (o.no_overlay, o.overlay_lines, o.overlay_scores) == (False, False, True)
    _req(url + "api/solve", {"paths": [P1], "overlay": {"on": True, "lines": True, "scores": False}})   # older caller
    _wait(url)
    assert (seen["opts"].no_overlay, seen["opts"].overlay_lines) == (False, True)


def test_annotations_route_returns_the_payload_vocabulary(ui, fake):
    url, app = ui
    assert _req(url + "api/annotations?path=" + urllib.request.quote(P1))["payload"] is None
    from astro_id.annotations import GROUPS, METRICS, SCORE
    fake.annotations[(P1, "astro-id")] = {"version": 1, "groups": GROUPS, "metrics": METRICS, "score": SCORE,
                                          "items": [{"id": "a"}, {"id": "b"}], "image": {"width": 1, "height": 1}}
    fake.annotations[(P1, "other")] = {"version": 1, "items": []}
    d = _req(url + "api/annotations?path=" + urllib.request.quote(P1))
    assert d["items"] == 2 and [g["id"] for g in d["payload"]["groups"]] == [g["id"] for g in GROUPS]
    assert d["payload"]["metrics"][0]["key"] == "mag" and d["payload"]["score"]["default"] == 65
    assert "items" not in d["payload"]                          # vocabulary only, no geometry
    with pytest.raises(urllib.error.HTTPError):
        _req(url + "api/annotations")


def test_adopt_takes_over_old_plugin_rows_read_only(fake, ledger, tmp_path):
    import sqlite3

    from astro_id.facet.legacy import adopt_plugin_rows

    db = tmp_path / "lib.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE photo_manual_tags (photo_path TEXT, tag TEXT, source TEXT, created_by TEXT)")
        c.execute("CREATE TABLE photo_annotations (photo_path TEXT, source TEXT, payload TEXT)")
        c.executemany("INSERT INTO photo_manual_tags VALUES (?, ?, 'user', ?)",
                      [(P1, "orion", "astro-id"), (P1, "plate-solved", "astro-id"), (P1, "my trip", None),
                       ("//elsewhere/x.jpg", "orion", "astro-id")])
        c.execute("INSERT INTO photo_annotations VALUES (?, 'astro-id', ?)", (P1, json.dumps({"version": 1})))
    before = db.read_bytes()
    lib = library_key(fake.url)
    out = adopt_plugin_rows(ledger, lib, str(db), path_prefix=ROOT)
    assert out == {"photos": 1, "adopted": 1, "tags": 2, "annotations": 1, "dry_run": False}
    assert ledger.tags(lib, P1) == ["orion", "plate-solved"] and ledger.get(lib, P1)["annotation_via"] == "legacy-db"
    assert db.read_bytes() == before
    fake.photos[P1]["manual_tags"] = ["my trip", "orion", "plate-solved"]
    ctx = ctx_for(fake, ledger)
    S.apply_results(ctx, {P1: frame(objs=[obj("taurus")])})      # adopted tags are now astro-id's to replace
    assert fake.photos[P1]["manual_tags"] == ["my trip", "plate-solved", "taurus"]
    assert adopt_plugin_rows(ledger, lib, str(db))["adopted"] == 1    # P1 is owned now; only the other path


def test_legacy_writer_works_from_another_thread(tmp_path):
    """Regression (E2E 2026-11-11): serve created the writer on the main thread and used it in the solve thread."""
    import sqlite3

    from astro_id.facet.legacy import LegacyAnnotationWriter

    (tmp_path / "facet" / "db").mkdir(parents=True)
    (tmp_path / "facet" / "db" / "annotations.py").write_text(LEGACY_MODULE, encoding="utf-8")
    db = tmp_path / "lib.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE photos (path TEXT PRIMARY KEY)")
        c.execute("CREATE TABLE photo_annotations (photo_path TEXT, source TEXT, payload TEXT, "
                  "PRIMARY KEY (photo_path, source))")
        c.execute("INSERT INTO photos VALUES (?)", (P1,))
    w = LegacyAnnotationWriter(str(db), str(tmp_path / "facet"))
    errs = []
    t = threading.Thread(target=lambda: errs.append(None) if w.upsert(P1, "astro-id", {"version": 1}) is None else 0)
    t.start()
    t.join()
    w.close()
    assert errs == [None]


def test_annotation_failure_still_records_the_tags_in_the_ledger(fake, ledger, monkeypatch):
    ctx = ctx_for(fake, ledger)

    def boom(*a, **kw):
        raise RuntimeError("database exploded")
    monkeypatch.setattr(ctx.client, "put_annotation", boom)
    rep = S.apply_results(ctx, {P1: frame(objs=[obj("orion")])})
    assert rep[0]["annotation"].startswith("error") and fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    assert ledger.tags(ctx.library, P1) == ["orion", "plate-solved"]


# ----------------------------------------------- panel redesign: count, auto time, export ---

def test_count_names_the_load_button_and_says_if_setup_is_saved(ui, fake, tmp_path):
    url, app = ui
    c = _req(url + "api/count", {"path_prefix": ROOT + "/"})
    assert c == {"count": 3, "setup": False}
    a = _req(url + "api/count", {"album_id": 1})
    assert (a["count"], a["name"], a["setup"], a["smart"]) == (2, "Night", False, False)
    app.save_album_config(1, {"clock_offset": "+1h"})
    assert _req(url + "api/count", {"album_id": 1})["setup"] is True
    # a photo opened from an album: is it in the album the user viewed last?
    members = {1: [{"path": P2}]}                                   # the fake's listing ignores album_id
    real = app.client.list_photos
    app.client.list_photos = lambda **kw: members[kw["album_id"]] if kw.get("album_id") else real(**kw)
    assert "contains" not in a and _req(url + "api/count", {"album_id": 1, "path": P1})["contains"] is False
    assert _req(url + "api/count", {"album_id": 1, "path": P2})["contains"] is True
    # the page checks this to tell a stale add-on process from a broken one
    from astro_id.facet.serve import PAGE_VERSION
    assert _req(url + "api/state")["page_version"] == PAGE_VERSION
    for bad in ({}, {"album_id": "x"}, {"album_id": 99}):
        with pytest.raises(urllib.error.HTTPError) as ei:
            _req(url + "api/count", bad)
        assert ei.value.code == 400


def test_time_fit_suggests_only_from_frames_that_pin_the_time():
    from astro_id.facet.serve import time_fit

    none = time_fit({P1: {"solved": True, "best_fit_delta_min": None}, P2: {"solved": False}}, "+2559d11h08m")
    assert none["frames"] == 0 and none["offset"] is None and none["tolerance_min"] is None
    tf = time_fit({P1: {"solved": True, "best_fit_delta_min": -2.4}, P2: {"solved": True, "best_fit_delta_min": -1.6},
                   P3: {"solved": True, "solver": "stack-copy", "best_fit_delta_min": 40}}, "+2559d11h08m")
    # median -2.0 min -> the offset moves 2 min earlier, whole minutes only; tolerance = spread 0.4 + 2 -> 3
    assert (tf["frames"], tf["offset"], tf["delta_min"], tf["tolerance_min"]) == (2, "+2559d11h06m", -2.0, 3)
    assert tf["files"] == ["A1.JPG", "A2.JPG"] and tf["pinned_by"] == "the Moon"
    assert time_fit({P1: {"solved": True, "best_fit_delta_min": 3}}, "")["offset"] == "+03m"
    assert time_fit({P1: {"solved": True, "best_fit_delta_min": 3}}, "nonsense")["offset"] is None


def test_done_carries_time_fit_and_setup_keeps_export_options(ui, fake, monkeypatch, tmp_path):
    url, app = ui
    _req(url + "api/photos", {"path_prefix": ROOT})
    _run_job(url, monkeypatch, lambda p: {**frame(objs=[obj("moon", 95, 2)]), "best_fit_delta_min": 1.0})
    _req(url + "api/solve", {"paths": [P1, P2], "offset": "+1h"})
    tf = _wait(url)["done"]["time_fit"]
    assert tf["frames"] == 2 and tf["offset"] == "+1h01m"
    assert _req(url + "api/state")["sky_vocab"]["groups"][0]["id"] == "constellation"
    # Export's options are part of the setup (saved automatically by the panel) and come back with the folder
    from astro_id.facet.serve import export_options
    assert export_options({"lines": False, "scores": True, "legend": "yes", "evil": 1}) == {"lines": False, "scores": True}
    d = tmp_path / "night"
    d.mkdir()
    monkeypatch.setattr(app, "local", lambda p: str(d / p.rsplit("/", 1)[-1]))
    _req(url + "api/save-config", {"paths": [P1], "export": {"lines": False, "scores": True, "legend": True}})
    assert json.loads((d / "astro-id.json").read_text())["export"] == {"lines": False, "scores": True, "legend": True}
    assert app.folder_config(P1)["export"] == {"lines": False, "scores": True, "legend": True}


PAYLOAD = {"version": 1, "source": "astro-id", "image": {"width": 400, "height": 300}, "legend": "2026-11-09 08:11 PST",
           "groups": [{"id": "constellation", "default": True}, {"id": "planet", "default": True},
                      {"id": "asterism", "default": True}, {"id": "star", "default": False}],
           "metrics": [{"key": "mag", "min": -5, "max": 8, "default": 6, "direction": "max"}],
           "score": {"min": 0, "max": 100, "default": 65},
           "items": [{"id": "orion", "label": "Orion", "group": "constellation", "score": 90, "shape": "lines",
                      "segments": [[[10, 10], [200, 150]]], "label_at": [100, 80]},
                     {"id": "jupiter", "label": "Jupiter", "group": "planet", "score": 70, "shape": "point",
                      "x": 300, "y": 100, "r": 18, "values": {"mag": -2.5}},
                     {"id": "weak", "label": "Weak", "group": "planet", "score": 40, "shape": "point", "x": 50, "y": 250},
                     {"id": "rigel", "label": "Rigel", "group": "star", "score": 95, "shape": "point", "x": 120,
                      "y": 200, "values": {"mag": 0.1}},
                     {"id": "faint", "label": "Faint", "group": "star", "score": 95, "shape": "point", "x": 130,
                      "y": 210, "values": {"mag": 5.5}},
                     {"id": "belt", "label": "Orion's Belt", "group": "asterism", "score": 90, "shape": "lines",
                      "segments": [[[150, 150], [170, 160]]]},
                     {"id": "alnitak", "label": "Alnitak", "group": "star", "score": 95, "shape": "point", "x": 160,
                      "y": 155, "member_of": "belt", "values": {"mag": 1.7}}]}


def test_export_shows_exactly_what_the_sky_label_view_shows():
    """Same filter as Facet's photo layer (annotations.ts settingsFromView + filterAnnotationItems)."""
    from astro_id.export import settings, visible_items

    ids = lambda view: [i["id"] for i in visible_items(PAYLOAD, settings(PAYLOAD, view))]
    assert ids({}) == ["orion", "jupiter", "belt"]                         # defaults: stars off, score >= 65
    assert ids({"score": 30}) == ["orion", "jupiter", "weak", "belt"]
    assert ids({"score": None}) == ["orion", "jupiter", "weak", "belt"]    # null turns the score filter off
    assert ids({"groups": {"star": True}}) == ["orion", "jupiter", "rigel", "faint", "belt"]   # Alnitak: its belt shows
    assert ids({"groups": {"star": True, "asterism": False}}) == ["orion", "jupiter", "rigel", "faint", "alnitak"]
    assert ids({"groups": {"star": True}, "metrics": {"mag": 2}}) == ["orion", "jupiter", "rigel", "belt"]
    assert ids({"groups": {"star": True}, "show_members": True})[-1] == "alnitak"
    assert ids({"score": 500}) == []                                        # clamped to the payload's max (100)
    assert settings(PAYLOAD, {"visible": False})["score"] == 65             # visible is not a filter for export


def _jpg(path, size=(800, 600)):
    from PIL import Image

    Image.new("RGB", size, (5, 5, 20)).save(path, "JPEG")
    return path


def test_export_route_renders_from_the_last_solve_without_solving_again(ui, fake, monkeypatch, tmp_path):
    from astro_id import export as EX

    url, app = ui
    _req(url + "api/photos", {"path_prefix": ROOT})
    night = tmp_path / "night"
    night.mkdir()
    src = _jpg(night / "A1.JPG")
    _run_job(url, monkeypatch, lambda p: {**frame(), "file": str(src), "annotations": PAYLOAD} if p == P1
             else frame(solved=False))
    _req(url + "api/solve", {"paths": [P1, P2]})
    _wait(url)
    calls = []
    real = EX.render
    monkeypatch.setattr(EX, "render", lambda *a, **kw: calls.append((a, kw)) or real(*a, **kw))
    import astro_id.session as session
    monkeypatch.setattr(session, "run_session", lambda *a, **kw: pytest.fail("export must not solve"))
    r = _req(url + "api/export", {"paths": [P1, P2], "view": {"score": 30, "groups": {"star": True}},
                                  "options": {"lines": False, "scores": True, "legend": True}})
    assert r == {"started": 2}
    for _ in range(200):
        st = _req(url + "api/export")
        if not st["running"]:
            break
        threading.Event().wait(0.05)
    out = tmp_path / "night-annotated" / "A1_astro.jpg"
    assert out.is_file() and st["done"] == 2 and [w["path"] for w in st["written"]] == [P1]
    assert st["written"][0]["items"] == 6 and st["folders"] == [str(tmp_path / "night-annotated").replace("\\", "/")]
    assert [e["path"] for e in st["errors"]] == [P2] and "sky labels" in st["errors"][0]["error"]
    (a, kw), = calls
    assert a[3] == {"score": 30, "groups": {"star": True}} and kw == {"lines": False, "scores": True, "legend": True}
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (800, 600)
    assert fake.writes() == []                                             # exporting writes nothing to Facet
    with pytest.raises(urllib.error.HTTPError) as ei:
        _req(url + "api/export", {"paths": []})
    assert ei.value.code == 400


def test_export_uses_the_sky_labels_stored_in_facet_after_a_reload(ui, fake, monkeypatch, tmp_path):
    url, app = ui
    _req(url + "api/photos", {"path_prefix": ROOT})
    src = _jpg(tmp_path / "A2.JPG", (400, 300))
    monkeypatch.setattr(app, "local", lambda p: str(src))
    fake.annotations[(P2, "astro-id")] = PAYLOAD                          # saved in an earlier session
    payload, path = app._export_source(P2)
    assert payload["items"] and path == str(src)
    with pytest.raises(ValueError, match="Save to Facet"):
        app._export_source(P1)                                             # nothing stored for P1
    from astro_id.export import render
    r = render(src, tmp_path / "out" / "A2_astro.jpg", payload, {}, lines=True, scores=False, legend=False)
    assert r["items"] == 3 and (tmp_path / "out" / "A2_astro.jpg").is_file()


def test_f9_two_export_clicks_start_one_export(ui, monkeypatch, tmp_path):
    import time as _t
    from astro_id import export as EX
    from astro_id.facet import serve as SVV

    url, app = ui
    release = threading.Event()
    monkeypatch.setattr(app, "_export_source", lambda p: ({"version": 1, "items": []}, str(tmp_path / "n" / "A.JPG")))
    monkeypatch.setattr(EX, "render", lambda *a, **kw: (release.wait(5), {"out": str(a[1]), "items": 0})[1])
    real = SVV.export_options
    monkeypatch.setattr(SVV, "export_options", lambda v: (_t.sleep(0.2), real(v))[1])   # widens the race window
    out = []

    def click():
        try:
            app.export(None, {"paths": [P1]})
            out.append("started")
        except ValueError as exc:
            out.append(str(exc))
    ts = [threading.Thread(target=click) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    release.set()
    assert sorted(out) == ["an export is already running", "started"]


def test_f9_a_raw_and_its_jpg_export_to_different_files(ui, monkeypatch, tmp_path):
    from astro_id import export as EX

    url, app = ui
    d = tmp_path / "night"
    d.mkdir()
    for n in ("IMG_1.CR2", "IMG_1.JPG", "IMG_1.xmp", "IMG_2.JPG", "IMG_2.xmp"):
        (d / n).write_bytes(b"x")
    monkeypatch.setattr(app, "_export_source", lambda p: ({"version": 1, "items": []}, str(d / p)))
    outs = []
    monkeypatch.setattr(EX, "render", lambda src, out, *a, **kw: outs.append(out.name) or {"out": str(out), "items": 0})
    job = {"running": True, "total": 3, "done": 0, "current": None, "written": [], "errors": [], "folders": []}
    app._export_run(job, ["IMG_1.CR2", "IMG_1.JPG", "IMG_2.JPG"], {}, {})
    assert outs == ["IMG_1_cr2_astro.jpg", "IMG_1_jpg_astro.jpg", "IMG_2_astro.jpg"] and not job["errors"]
