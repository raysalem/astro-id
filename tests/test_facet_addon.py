"""astro-id as a Facet add-on (PLUGIN_BOUNDARY.md v0.2): manifest install/uninstall, the supervised process
(port line, HMAC health, launch-secret gate, path refusal, no cookies), the scope -> photo-set mapping, thumbnails
through the backend, the add-on credential, and the bridge script (Node, when available)."""
from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path

import pytest

from astro_id.facet import install as I
from astro_id.facet import serve as SV
from astro_id.facet.client import FacetClient, addon_credentials
from astro_id.facet.ledger import Ledger
from fake_facet import FakeFacet

ROOT = "//nas/Photos/night"
P1, P2, P3 = f"{ROOT}/A1.JPG", f"{ROOT}/A2.JPG", f"{ROOT}/A3.JPG"
SECRET = "launch-secret-0123456789abcdef"
TOKEN = "fct_addon_token_for_tests"
REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "astro_id" / "facet" / "static"


def photos():
    return [
        {"path": P1, "filename": "A1.JPG", "category": "astro", "date_taken": "2019:11:05 09:31:59",
         "gps_latitude": 32.5, "gps_longitude": -116.25, "scan_run": 7},
        {"path": P2, "filename": "A2.JPG", "category": "astro", "date_taken": "2019:11:05 09:40:00", "scan_run": 7,
         "manual_tags": ["night"]},
        {"path": P3, "filename": "A3.JPG", "category": "landscape", "date_taken": "2019:11:06 01:00:00",
         "scan_run": 7},
    ]


@pytest.fixture()
def fake():
    with FakeFacet(photos(), addon_token=TOKEN) as f:
        yield f


def addon_env(fake, **kw):
    e = {"FACET_URL": fake.url, "FACET_TOKEN": TOKEN, "FACET_ADDON_ID": "astro-id", "FACET_LAUNCH_SECRET": SECRET}
    e.update(kw)
    return {k: v for k, v in e.items() if v is not None}


@pytest.fixture()
def addon(fake, tmp_path):
    """An add-on server in this process (as Facet's supervisor would start it), plus a raw HTTP helper."""
    args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                          "path_map": None})()
    server, box, aenv = SV.build_addon(args, addon_env(fake))
    app = SV.finish_addon(args, box, aenv)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    port = server.server_address[1]

    def req(method, path, body=None, headers=None, launch=True):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"X-Facet-Launch": SECRET} if launch else {}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        data = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        doc = json.loads(data) if "json" in hdrs.get("content-type", "") and data else data
        return r.status, hdrs, doc

    yield req, app, port
    server.shutdown()
    server.server_close()
    app.ledger.close()


# ---------------------------------------------------------------- install ---

def _fake_uv(tmp_path) -> Path:
    uv = tmp_path / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    uv.parent.mkdir()
    uv.write_bytes(b"")
    return uv


def _tree(d: Path) -> set[str]:
    return {str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*")}


def test_install_writes_only_the_manifest(tmp_path, monkeypatch):
    data = tmp_path / "facet-data"
    data.mkdir()
    (data / "scoring_config.json").write_text("{}")
    uv = _fake_uv(tmp_path)
    monkeypatch.setenv("UV", str(uv))
    before = _tree(data)
    target, doc = I.install(str(data))
    assert target == data.resolve() / "addons" / "astro-id" / "addon.json"
    assert _tree(data) - before == {"addons", "addons/astro-id", "addons/astro-id/addon.json"}
    assert (data / "scoring_config.json").read_text() == "{}"        # Facet's config is never touched
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == doc
    assert doc["id"] == "astro-id" and doc["name"] == "Astro" and doc["icon"] == "star" and doc["facet_api"] == 1
    assert doc["ui"] == {"path": "/ui/"} and doc["slots"] == ["sidebar", "import_hook"]
    assert doc["permissions"] == ["photos.read", "tags.write", "annotations.write", "gps.write"]
    st = doc["start"]
    assert st["port"] == "auto" and st["health"] == "/health"
    argv = st["argv"]
    assert Path(argv[0]).is_absolute() and Path(argv[0]) == uv.resolve()
    assert argv[1:3] == ["run", "--project"] and Path(argv[3]) == REPO and (Path(argv[3]) / "pyproject.toml").is_file()
    assert argv[4:] == ["astro-id", "facet", "serve", "--addon"]
    assert "url" not in doc["ui"]                                         # start and ui.url are exclusive
    assert re.match(r"^[a-z0-9][a-z0-9_-]*$", doc["id"])                  # Facet's annotation source id shape
    # idempotent re-install, then uninstall removes the manifest and its folder only
    I.install(str(data))
    assert _tree(data) - before == {"addons", "addons/astro-id", "addons/astro-id/addon.json"}
    target2, existed = I.uninstall(str(data))
    assert existed and not target2.exists() and not (data / "addons" / "astro-id").exists()
    assert (data / "addons").is_dir() and (data / "scoring_config.json").is_file()
    assert I.uninstall(str(data))[1] is False


def test_install_refuses_missing_data_dir_and_uv(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="data folder"):
        I.install(str(tmp_path / "nope"))
    (tmp_path / "d").mkdir()
    monkeypatch.setenv("UV", str(tmp_path / "missing-uv.exe"))
    with pytest.raises(ValueError, match="uv not found"):
        I.install(str(tmp_path / "d"))
    assert not (tmp_path / "d" / "addons").exists()


def test_install_cli(tmp_path, monkeypatch, capsys):
    from astro_id import cli

    data = tmp_path / "fd"
    data.mkdir()
    monkeypatch.setenv("UV", str(_fake_uv(tmp_path)))
    assert cli.main(["facet", "install", "--facet-data", str(data)]) == 0
    out = capsys.readouterr().out
    assert "addon.json" in out and (data / "addons" / "astro-id" / "addon.json").is_file()
    assert cli.main(["facet", "uninstall", "--facet-data", str(data)]) == 0
    assert not (data / "addons" / "astro-id").exists()
    assert cli.main(["facet", "install", "--facet-data", str(tmp_path / "missing")]) == 2


# ------------------------------------------------------- process contract ---

def test_health_answers_hmac_without_the_launch_header(addon):
    req, _, _ = addon
    st, h, doc = req("GET", "/health?c=abc123", launch=False)
    assert st == 200 and doc == {"addon": "astro-id",
                                 "ack": hmac.new(SECRET.encode(), b"abc123", sha256).hexdigest()}
    assert req("GET", "/health", launch=False)[0] == 400
    assert req("GET", "/health?c=" + "x" * 600, launch=False)[0] == 400


def test_launch_secret_gate_on_everything_else(addon):
    req, _, _ = addon
    for path in ("/ui/", "/ui/api/state", "/", "/nonexistent"):
        assert req("GET", path, launch=False)[0] == 403
        assert req("GET", path, headers={"X-Facet-Launch": SECRET[:-1] + "X"}, launch=False)[0] == 403
    assert req("POST", "/ui/api/scope", {"kind": "selection", "paths": [P1]}, launch=False)[0] == 403
    assert req("OPTIONS", "/ui/api/state", launch=False)[0] == 403
    st, h, body = req("GET", "/ui/")
    assert st == 200 and h["content-type"].startswith("text/html") and b"Astro session" in body
    assert b"AstroBridge" in body and b"<!--ASTRO_BRIDGE-->" not in body   # bridge inlined, one request
    assert req("GET", "/ui/api/state")[0] == 200
    assert req("OPTIONS", "/ui/api/state")[0] == 204
    st, h, _ = req("GET", "/ui")
    assert st == 308 and h["location"] == "ui/"                            # relative: survives the proxy prefix
    assert req("GET", "/other")[0] == 404 and req("POST", "/ui/", {})[0] == 404
    assert req("PUT", "/ui/api/state", {})[0] == 405


def test_path_refusal(addon):
    req, _, _ = addon
    for bad in ("/ui/../ui/", "/ui/%2e%2e/ui/", "/ui/%2E%2E/api/state", "/ui//api/state", "/ui/%5capi/state",
                "/ui/api%00/state", "/ui/./api/state", "/%2e%2e/health"):
        assert req("GET", bad)[0] == 400, bad
    assert SV.safe_rel_path("/ui/%252e%252e/x") == "/ui/%2e%2e/x"   # decoded once only: no double decode
    assert SV.safe_rel_path("/ui/") == "/ui/" and SV.safe_rel_path("ui/") is None


def test_no_cookies_and_headers(addon):
    req, _, _ = addon
    for method, path, body in (("GET", "/ui/", None), ("GET", "/ui/api/state", None), ("GET", "/health?c=1", None),
                               ("POST", "/ui/api/scope", {"kind": "nope"}), ("GET", "/ui/missing", None)):
        st, h, _ = req(method, path, body)
        assert "set-cookie" not in h, (method, path)
        assert h.get("cache-control") == "no-store" and h.get("x-content-type-options") == "nosniff"
    st, h, _ = req("GET", "/ui/")
    assert h["content-security-policy"].startswith("sandbox allow-scripts")
    assert "form-action 'none'" in h["content-security-policy"]


def test_forwarded_prefix_is_recorded_not_trusted_blindly(addon):
    req, app, _ = addon
    req("GET", "/ui/api/state", headers={"X-Forwarded-Prefix": "/addons/astro-id"})
    assert app.mount == "/addons/astro-id"
    req("GET", "/ui/api/state", headers={"X-Forwarded-Prefix": "https://evil.example/x"})
    assert app.mount == "/addons/astro-id"
    st, _, doc = req("GET", "/ui/api/state")
    assert doc["addon"] == "astro-id" and doc["mount"] == "/addons/astro-id" and doc["tags_via"] == "manual+ledger"


def test_state_uses_the_token_and_annotations_note(fake, addon):
    req, app, _ = addon
    st, _, doc = req("GET", "/ui/api/state")
    assert st == 200 and doc["can_write"] and doc["annotations"] == "api"
    assert any(c[1] == "/api/config" for c in fake.calls)
    assert app.client.static_token == TOKEN and app.client.scoped


def test_addon_without_annotation_route_skips_with_a_visible_note(tmp_path):
    with FakeFacet(photos(), addon_token=TOKEN, addon_denied={("PUT", "/api/photo/annotations")}) as fake:
        args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                              "path_map": None})()
        server, box, aenv = SV.build_addon(args, addon_env(fake))
        try:
            app = SV.finish_addon(args, box, aenv)
            st = app.state({}, {})
            assert st["annotations"] is None and any("Annotations skipped" in w for w in st["warnings"])
            app.ledger.close()
        finally:
            server.server_close()
        # legacy DB writes are refused in add-on mode
        args.legacy_db = str(tmp_path / "x.db")
        with pytest.raises(ValueError, match="add-on mode"):
            SV.build_addon(args, addon_env(fake))


def test_addon_env_requires_the_supervisor_variables(fake):
    with pytest.raises(ValueError, match="FACET_LAUNCH_SECRET"):
        SV.AddonEnv(addon_env(fake, FACET_LAUNCH_SECRET=None))
    with pytest.raises(ValueError, match="FACET_URL"):
        SV.AddonEnv(addon_env(fake, FACET_URL=None))
    with pytest.raises(ValueError, match="too short"):
        SV.AddonEnv(addon_env(fake, FACET_LAUNCH_SECRET="short"))
    assert SV.AddonEnv(addon_env(fake, FACET_PORT="5123")).port == 5123


def test_addon_credentials_token_then_temporary_env_file(tmp_path):
    kw, note = addon_credentials("astro-id", {"FACET_TOKEN": TOKEN})
    assert kw == {"token": TOKEN} and note is None
    f = tmp_path / "creds.env"
    f.write_text("# temporary\nFACET_EDITION_PASSWORD='pw'\nOTHER=x\n", encoding="utf-8")
    kw, note = addon_credentials("astro-id", {"ASTRO_ID_ADDON_ENV": str(f)})
    assert kw["edition_password"] == "pw" and "TEMPORARY" in note
    kw, note = addon_credentials("astro-id", {"ASTRO_ID_ADDON_ENV": str(tmp_path / "none.env")})
    assert kw == {} and "read-only" in note


def test_scoped_token_feature_probe_treats_403_as_absent():
    with FakeFacet(photos(), addon_token=TOKEN, plugin_tags=True,
                   addon_denied={("PUT", "/api/photo/annotations")}) as fake:
        f = FacetClient(fake.url, token=TOKEN).features()
        assert f["annotations_put"] is False and f["plugin_tags"] is True
        # a password login (not scoped) keeps the old meaning: 401/403 = the route exists
        assert FacetClient(fake.url, edition_password="pw").features()["annotations_put"] is True


def test_port_line_in_a_real_process(fake, tmp_path):
    """`astro-id facet serve --addon` as Facet spawns it: exactly one stdout line, then /health answers."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FACET_", "ASTRO_ID_"))}
    env.update(addon_env(fake), ASTRO_ID_LEDGER=str(tmp_path / "l.sqlite"), PYTHONUNBUFFERED="0")
    code = "import sys; from astro_id.cli import main; sys.exit(main(sys.argv[1:]))"
    p = subprocess.Popen([sys.executable, "-c", code, "facet", "serve", "--addon"], cwd=str(REPO), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = p.stdout.readline()
        assert re.fullmatch(r"FACET_ADDON_PORT=\d+\n", line), line
        port = int(line.split("=")[1])
        for _ in range(100):
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("GET", "/health?c=xyz")
                r = c.getresponse()
                doc = json.loads(r.read())
                break
            except OSError:
                time.sleep(0.05)
        assert doc["ack"] == hmac.new(SECRET.encode(), b"xyz", sha256).hexdigest()
    finally:
        p.terminate()
        out, err = p.communicate(timeout=10)
    assert out == ""                                  # nothing else ever goes to stdout
    assert "FACET_ADDON_PORT" not in err


# ------------------------------------------------------ scope + thumbnails ---

def test_scope_import_maps_to_scan_run(fake, addon):
    req, _, _ = addon
    st, _, d = req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7", "category": "astro"})
    assert st == 200 and [p["path"] for p in d["photos"]] == [P1, P2] and d["scope"]["kind"] == "import"
    call = [c for c in fake.calls if c[1] == "/api/photos"][-1]
    assert call[2]["scan_run"] == "7" and call[2]["category"] == "astro" and call[2]["hide_bursts"] == "0"
    st, _, d = req("POST", "/ui/api/scope", {"kind": "import", "run_id": 7, "category": ""})
    assert len(d["photos"]) == 3
    assert req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7; drop"})[0] == 400
    assert req("POST", "/ui/api/scope", {"kind": "import"})[0] == 400


def test_scope_view_passes_the_gallery_query_minus_paging(fake, addon):
    req, _, _ = addon
    st, _, d = req("POST", "/ui/api/scope",
                   {"kind": "view", "query": "?category=astro&page=3&per_page=10&sort=score&hide_bursts=1&tag=night"})
    assert st == 200 and [p["path"] for p in d["photos"]] == [P2]
    q = [c for c in fake.calls if c[1] == "/api/photos"][-1][2]
    assert q["page"] == "1" and q["per_page"] == "500" and q["sort"] == "date_taken" and q["hide_bursts"] == "0"
    assert q["category"] == "astro" and q["tag"] == "night"
    st, _, d = req("POST", "/ui/api/scope", {"kind": "view", "query": {"category": "landscape", "nonce": "x"}})
    assert [p["path"] for p in d["photos"]] == [P3]
    assert "nonce" not in [c for c in fake.calls if c[1] == "/api/photos"][-1][2]
    assert req("POST", "/ui/api/scope", {"kind": "view", "query": "a=" + "x" * 2100})[0] == 400
    assert req("POST", "/ui/api/scope", {"kind": "view", "query": 5})[0] == 400


def test_scope_selection_and_limits(fake, addon):
    req, _, _ = addon
    st, _, d = req("POST", "/ui/api/scope", {"kind": "selection", "paths": [P3, P1, P1, f"{ROOT}/gone.jpg"]})
    assert st == 200 and [p["path"] for p in d["photos"]] == [P3, P1] and d["scope"]["requested"] == 4
    assert req("POST", "/ui/api/scope", {"kind": "selection", "paths": [P1] * 501})[0] == 400
    assert req("POST", "/ui/api/scope", {"kind": "selection", "paths": "x"})[0] == 400
    assert req("POST", "/ui/api/scope", {"kind": "album"})[0] == 400


def test_thumbnails_only_for_the_current_set_through_the_backend(fake, addon):
    req, _, _ = addon
    assert req("GET", "/ui/api/thumb?path=" + P1)[0] == 404        # nothing loaded yet
    req("POST", "/ui/api/scope", {"kind": "selection", "paths": [P1]})
    st, h, body = req("GET", "/ui/api/thumb?size=96&path=" + P1.replace("/", "%2F"))
    assert st == 200 and h["content-type"] == "image/jpeg" and body.endswith(P1.encode())
    call = [c for c in fake.calls if c[1] == "/thumbnail"][-1]
    assert call[2] == {"path": P1, "size": "96"}
    assert req("GET", "/ui/api/thumb?path=" + P2)[0] == 404        # not in the set
    assert req("GET", "/ui/api/thumb?path=" + P1, launch=False)[0] == 403


def test_gps_write_goes_through_put_gps(fake, addon):
    req, _, _ = addon
    req("POST", "/ui/api/scope", {"kind": "selection", "paths": [P2]})
    st, _, d = req("POST", "/ui/api/gps", {"path": P2, "text": "32.61, -116.32"})
    assert st == 200 and d["gps"] == [32.61, -116.32] and d["gps_source"] == "facet-manual"
    put = [c for c in fake.calls if c[1] == "/api/photo/gps"][-1]
    assert put[0] == "PUT"


def test_addon_solve_writes_tags_with_the_token_and_never_cull_flags(fake, addon, monkeypatch):
    from astro_id import session

    req, app, _ = addon

    def fake_run(paths, opts, emit):
        res = {p: {"solved": True, "objects": [{"tag": "orion", "confidence": 90, "level": 1}],
                   "quality": {"detected": 900, "fwhm_px": 5.0, "elongation": 1.1},
                   "annotations": {"version": 1, "source": "astro-id", "image": {"width": 1, "height": 1},
                                   "items": []}} for p in paths}
        done = {"event": "done", "solved": len(paths), "elapsed_s": 0.1, "time": {},
                "cull": {"reference": {}, "rule": {}, "frames": {p: {"decision": "keep", "reasons": []}
                                                                  for p in paths}}}
        emit(done)
        return {**done, "results": res}
    monkeypatch.setattr(session, "run_session", fake_run)
    req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7", "category": "astro"})
    st, _, r = req("POST", "/ui/api/solve", {"paths": [P1, P2], "tolerance_min": "5"})
    assert st == 200 and r["started"] == 2
    for _ in range(200):
        st, _, j = req("GET", "/ui/api/job?after=0")
        if not j["running"]:
            break
        time.sleep(0.02)
    assert j["error"] is None and {r["path"] for r in j["reports"]} == {P1, P2}
    assert fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    assert (P1, "astro-id") in fake.annotations
    tag_calls = [c for c in fake.calls if c[1] == "/api/photos/batch_manual_tags"]
    assert tag_calls and not any("reject" in c[1] or "cull" in c[1] for c in fake.calls)


# --------------------------------------------------------------- the page ---

def test_page_has_no_storage_cookies_popups_downloads_or_forms():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "bridge.js").read_text(encoding="utf-8")
    for text in (html, js):
        for banned in ("localStorage", "sessionStorage", "document.cookie", "window.open", "confirm(", "alert(",
                       "prompt(", "<form", "serviceWorker", "indexedDB"):
            assert banned not in text, banned
        assert not re.search(r"download\s*=|\.download\b", text)          # no download links
    # every fetch goes through the bridge wrapper (relative URLs + X-Addon-Nonce)
    assert html.count("fetch(") == 1 and "B.makeApi((u, o) => fetch(u, o), NONCE)" in html
    assert not re.search(r"""api\(\s*["'`]/""", html)                      # no absolute API paths
    assert "<!--ASTRO_BRIDGE-->" in html and "</script" not in js.lower()


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_bridge_js_under_node():
    r = subprocess.run([NODE, "--test", "--test-reporter=tap", str(REPO / "tests" / "js" / "bridge.test.js")], capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"# pass (\d+)", r.stdout)
    assert m and int(m.group(1)) >= 8, r.stdout


def test_lifeline_watches_the_chain_through_uv_and_facet():
    from astro_id.facet.lifeline import ancestors_to_watch

    # python(10) <- venv launcher(9) <- astro-id.exe(8) <- uv.exe(7) <- Facet python(6) <- explorer(5)
    table = {10: (9, "python.exe"), 9: (8, "python.exe"), 8: (7, "astro-id.exe"), 7: (6, "uv.exe"),
             6: (5, "python.exe"), 5: (1, "explorer.exe")}
    assert ancestors_to_watch(10, table) == [9, 8, 7, 6]
    # no uv in the chain (run by hand): just the parent
    assert ancestors_to_watch(10, {10: (9, "python.exe"), 9: (4, "cmd.exe"), 4: (0, "x")}) == [9]
    assert ancestors_to_watch(10, {10: (99, "python.exe")}) == []          # parent already gone
