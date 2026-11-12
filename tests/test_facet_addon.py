"""astro-id as a Facet add-on (PLUGIN_BOUNDARY.md v0.3): manifest install/uninstall, the supervised process
(port line, HMAC health, launch-secret gate, path refusal, no cookies), the scope -> photo-set mapping over MCP
(R-MCP, against the stub in fake_facet_mcp.py), thumbnails through the backend, the add-on credential, the folder /
album location (R-LOC), and the bridge script (Node, when available)."""
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
from astro_id.facet.client import FacetClient, FacetError, addon_credentials, connect
from astro_id.facet.mcp_client import McpFacetClient
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
         "gps_latitude": 32.5, "gps_longitude": -116.25, "scan_run": 7, "albums": [1]},
        {"path": P2, "filename": "A2.JPG", "category": "astro", "date_taken": "2019:11:05 09:40:00", "scan_run": 7,
         "manual_tags": ["night"]},
        {"path": P3, "filename": "A3.JPG", "category": "landscape", "date_taken": "2019:11:06 01:00:00",
         "scan_run": 7},
    ]


@pytest.fixture()
def fake():
    with FakeFacet(photos(), addon_token=TOKEN) as f:
        yield f


def mcp_calls(fake, tool):
    return [args for name, args in fake.mcp_calls if name == tool]


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
    assert doc["addon"] == "astro-id" and doc["mount"] == "/addons/astro-id" and doc["transport"] == "mcp"
    assert doc["tags_via"].startswith("mcp add_tags")


def test_state_uses_the_token_and_annotations_note(fake, addon):
    req, app, _ = addon
    st, _, doc = req("GET", "/ui/api/state")
    # R-MCP: an fct_ token talks to Facet over /mcp; annotations are not on MCP until Facet PR-1.
    assert st == 200 and doc["can_write"] and doc["annotations"] is None and doc["version"] == "1.29.0"
    assert any("Annotations skipped" in w for w in doc["warnings"])
    assert isinstance(app.client, McpFacetClient) and app.client.static_token == TOKEN and app.client.scoped
    assert mcp_calls(fake, "status") and not any(c[1].startswith("/api/") for c in fake.calls)


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
    f = mcp_calls(fake, "search_photos")[-1]["filters"]
    assert f["scan_run"] == 7 and f["category"] == "astro" and f["hide_bursts"] is False
    st, _, d = req("POST", "/ui/api/scope", {"kind": "import", "run_id": 7, "category": ""})
    assert len(d["photos"]) == 3
    assert req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7; drop"})[0] == 400
    assert req("POST", "/ui/api/scope", {"kind": "import"})[0] == 400


def test_scope_view_passes_the_gallery_query_minus_paging(fake, addon):
    req, _, _ = addon
    st, _, d = req("POST", "/ui/api/scope",
                   {"kind": "view", "query": "?category=astro&page=3&per_page=10&sort=score&hide_bursts=1&tag=night"})
    assert st == 200 and [p["path"] for p in d["photos"]] == [P2]
    a = mcp_calls(fake, "search_photos")[-1]
    assert a["page"] == 1 and a["per_page"] == 100 and a["show"] is False and a["filters"]["hide_bursts"] is False
    assert a["filters"]["category"] == "astro" and a["filters"]["tag"] == "night"
    st, _, d = req("POST", "/ui/api/scope", {"kind": "view", "query": {"category": "landscape", "nonce": "x"}})
    assert [p["path"] for p in d["photos"]] == [P3]
    assert "nonce" not in mcp_calls(fake, "search_photos")[-1]["filters"]
    # a gallery key Facet's search_photos does not take is dropped, not fatal
    st, _, d = req("POST", "/ui/api/scope", {"kind": "view", "query": "?category=landscape&semanticQuery=stars"})
    assert st == 200 and [p["path"] for p in d["photos"]] == [P3]
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


def test_location_is_set_for_a_folder_after_one_confirm(fake, addon, tmp_path):
    req, app, _ = addon
    app.elevation = lambda lat, lon: 1234.0
    req("POST", "/ui/api/scope", {"kind": "import", "run_id": "7", "category": ""})
    earth = "https://earth.google.com/web/@32.6064,-116.3239,1103.5a,780d,35y,0h,0t,0r"
    st, _, d = req("POST", "/ui/api/location", {"scope": {"folder": ROOT}, "text": earth})
    # the check: parsed lat/lon/elevation, the scope and its counts; nothing written yet (a dry run)
    assert st == 200 and (d["lat"], d["lon"], d["elev"]) == (32.6064, -116.3239, 1103.5), d
    assert d["scope"] == {"kind": "folder", "folder": ROOT, "name": "night"} and d["confirmed"] is False
    assert (d["changed"], d["skipped"], d["photos"]) == (2, 1, 3)               # P1 keeps its camera GPS
    assert mcp_calls(fake, "set_gps")[-1]["dry_run"] is True and fake.photos[P2].get("gps_latitude") is None
    st, _, d = req("POST", "/ui/api/location", {"scope": {"folder": ROOT}, "text": earth, "confirm": True,
                                                "name": "TDS"})
    assert st == 200 and d["confirmed"] and d["changed"] == 2
    assert fake.photos[P2]["gps_latitude"] == 32.6064 and fake.photos[P2]["gps_manually_set"] == 1
    assert fake.photos[P1]["gps_latitude"] == 32.5                                # camera GPS kept
    a = mcp_calls(fake, "set_gps")[-1]
    assert a["path_prefix"] == ROOT and a["album_id"] is None and a["overwrite"] is False
    st, _, s = req("GET", "/ui/api/state")
    assert s["last_site"] == {"name": "TDS", "lat": 32.6064, "lon": -116.3239, "elev_m": 1103.5}
    # overwrite replaces camera GPS too; a typed decimal without elevation looks it up
    st, _, d = req("POST", "/ui/api/location", {"scope": {"folder": ROOT}, "text": "32.7, -116.4", "overwrite": True})
    assert d["elev"] == 1234.0 and d["changed"] == 3
    assert not any(c[1] == "/api/photo/gps" for c in fake.calls)                  # never the per-photo route


def test_location_scope_is_a_folder_or_an_album_never_photos(fake, addon):
    req, _, _ = addon
    req("POST", "/ui/api/scope", {"kind": "selection", "paths": [P1, P2]})
    text = "32.6, -116.3"
    for body in ({"scope": {}, "text": text}, {"scope": {"folder": ROOT, "album_id": 1}, "text": text},
                 {"scope": {"folder": "//elsewhere"}, "text": text}, {"scope": {"album_id": 99}, "text": text},
                 {"scope": {"folder": ROOT}, "path": P1, "text": text},
                 {"scope": {"folder": ROOT}, "paths": [P1], "text": text},
                 {"scope": {"folder": ROOT}, "text": "no coordinates"}):
        assert req("POST", "/ui/api/location", body)[0] == 400, body
    assert not mcp_calls(fake, "set_gps")
    st, _, d = req("POST", "/ui/api/location", {"scope": {"album_id": 1}, "text": text + ", 900", "confirm": True})
    assert st == 200 and d["scope"] == {"kind": "album", "album_id": 1, "name": "Night"} and d["elev"] == 900.0
    assert mcp_calls(fake, "set_gps")[-1]["album_id"] == 1 and d["changed"] == 0   # P1 (the album) has camera GPS


def test_location_saves_the_folder_site_for_solving(tmp_path):
    folder = tmp_path / "night"
    folder.mkdir()
    (folder / "astro-id.json").write_text(json.dumps({"clock_offset": "+1h"}), encoding="utf-8")
    root = str(folder).replace("\\", "/")
    rows = [{"path": f"{root}/B1.JPG", "filename": "B1.JPG", "scan_run": 3}]
    with FakeFacet(rows, addon_token=TOKEN) as fake:
        args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                              "path_map": None})()
        server, box, aenv = SV.build_addon(args, addon_env(fake))
        try:
            app = SV.finish_addon(args, box, aenv)
            app.elevation = lambda lat, lon: None
            app.scope({}, {"kind": "import", "run_id": "3"})
            r = app.location({}, {"scope": {"folder": root}, "text": "32.6, -116.3", "confirm": True, "name": "TDS"})
            assert r["saved"] == [f"{root}/astro-id.json"] and r["elev"] is None
            saved = json.loads((folder / "astro-id.json").read_text(encoding="utf-8"))
            assert saved["clock_offset"] == "+1h" and saved["site"]["name"] == "TDS"
            assert (saved["site"]["lat"], saved["site"]["lon"]) == (32.6, -116.3)
            app.ledger.close()
        finally:
            server.server_close()


def test_album_setup_is_saved_per_album_and_prefills_the_album(tmp_path):
    """Save setup with an album: every folder's astro-id.json plus the album's own setup (ledger meta); loading the
    album returns it first. An older astro-id.json "overlay" reads as labeled copies and is replaced on save."""
    folder = tmp_path / "night"
    folder.mkdir()
    (folder / "astro-id.json").write_text(json.dumps({"overlay": {"on": True, "lines": False, "scores": True}}),
                                          encoding="utf-8")
    root = str(folder).replace("\\", "/")
    rows = [{"path": f"{root}/B1.JPG", "filename": "B1.JPG", "albums": [1]}]
    with FakeFacet(rows, addon_token=TOKEN) as fake:
        args = type("A", (), {"ledger": str(tmp_path / "l.sqlite"), "legacy_db": None, "no_annotations": False,
                              "path_map": None})()
        server, box, aenv = SV.build_addon(args, addon_env(fake))
        try:
            app = SV.finish_addon(args, box, aenv)
            d = app.list_photos({}, {"album_id": 1})
            assert d["album_config"] is None
            assert d["folders"][root]["labeled_copies"] == {"on": True, "lines": False, "scores": True}
            view = {"visible": True, "groups": {"star": True, "planet": False}, "score": 80, "metrics": {"mag": 4.5},
                    "show_scores": True, "junk": 1}
            body = {"paths": [f"{root}/B1.JPG"], "offset": "+1h", "tolerance_min": "5", "tz": "America/Los_Angeles",
                    "source": "raw", "labeled_copies": {"on": False, "lines": True, "scores": False},
                    "sky_labels": view, "album_id": 1}
            r = app.save_config({}, body)
            assert r["written"] == [f"{root}/astro-id.json"] and r["album"] == {"album_id": 1, "changed": True}
            saved = json.loads((folder / "astro-id.json").read_text(encoding="utf-8"))
            assert "overlay" not in saved and saved["labeled_copies"] == {"on": False, "lines": True, "scores": False}
            want_view = {"groups": {"star": True, "planet": False}, "score": 80, "metrics": {"mag": 4.5},
                         "show_scores": True}
            assert saved["sky_labels"] == want_view and saved["clock_offset"] == "+1h00m"
            a = app.list_photos({}, {"album_id": 1})["album_config"]
            assert a == {"clock_offset": "+1h00m", "time_tolerance_min": 5, "timezone": "America/Los_Angeles",
                         "solve_source": "raw", "labeled_copies": {"on": False, "lines": True, "scores": False},
                         "sky_labels": want_view}
            assert app.save_config({}, body)["album"]["changed"] is False          # same setup: unchanged
            assert app.list_photos({}, {"path_prefix": root})["scope"] == {"kind": "manual"}
            assert "album_config" not in app.list_photos({}, {"path_prefix": root})
            # a second album keeps its own setup
            app.save_album_config(2, {"clock_offset": "-2h"})
            assert app.album_config(2) == {"clock_offset": "-2h"} and app.album_config(1)["clock_offset"] == "+1h00m"
            with pytest.raises(ValueError):
                app.album_config("x")
            app.ledger.close()
        finally:
            server.server_close()


def test_mcp_client_against_the_stub(fake):
    c = connect(fake.url, token=TOKEN)
    assert isinstance(c, McpFacetClient)
    assert c.tools() == {"status", "search_photos", "get_photo", "filter_options", "list_albums", "add_tags",
                         "remove_tags", "set_gps"}
    assert c.server_version() == "1.29.0"
    assert [p["path"] for p in c.list_photos(extra={"scan_run": "7"})] == [P1, P2, P3]
    assert c.photo(P2)["manual_tags"] == ["night"] and c.photo(f"{ROOT}/gone.jpg") is None
    assert c.albums() == [{"id": 1, "name": "Night", "photo_count": 1}]
    assert c.batch_add_tag("orion", [P1, P2]) == 2 and fake.photos[P1]["tag_created_by"]["orion"] == "astro-id"
    assert c.delete_tag(P1, "orion") is True and c.delete_tag(P1, "orion") is False
    with pytest.raises(ValueError):
        c.set_location(1.0, 2.0)
    with pytest.raises(ValueError):
        c.set_location(1.0, 2.0, path_prefix=ROOT, album_id=1)
    with pytest.raises(FacetError) as e:
        c.call("delete_photos", {"paths": [P1]})
    assert e.value.status == 403 and e.value.code == "forbidden_level"
    # the bearer is what opens /mcp
    with pytest.raises(FacetError):
        connect(fake.url, token="fct_wrong").tools()
    assert not connect(fake.url, token=TOKEN).features()["annotations_put"]


def test_mcp_annotations_with_facet_pr1(tmp_path):
    from tests.fake_facet_mcp import ANNOTATION_TOOLS, ASTRO_TOOLS
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools=set(ASTRO_TOOLS | ANNOTATION_TOOLS)) as fake:
        c = connect(fake.url, token=TOKEN)
        assert c.features()["annotations_put"] is True
        payload = {"version": 1, "image": {"width": 10, "height": 10}, "items": []}
        assert c.put_annotation(P1, "astro-id", payload)["source"] == "astro-id"
        assert c.annotations(P1) == [{"source": "astro-id", "payload": payload}]
        assert c.delete_annotation(P1, "astro-id") is True and c.annotations(P1) == []


def test_mcp_allowlist_narrows_what_the_addon_sees(tmp_path):
    with FakeFacet(photos(), addon_token=TOKEN, mcp_tools={"status", "search_photos", "get_photo"}) as fake:
        c = connect(fake.url, token=TOKEN)
        assert c.tools() == {"status", "search_photos", "get_photo"}
        f = c.features()
        assert f["tags"] is False and f["gps"] is False
        with pytest.raises(FacetError) as e:
            c.set_location(1.0, 2.0, path_prefix=ROOT)
        assert e.value.code == "forbidden_level"


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
    assert j["error"] is None and not j["reports"] and not mcp_calls(fake, "add_tags")   # solving writes nothing
    st, _, s = req("POST", "/ui/api/save", {})
    assert st == 200 and {r["path"] for r in s["reports"]} == {P1, P2} and s["sky_labels"] == 0
    assert fake.photos[P1]["manual_tags"] == ["orion", "plate-solved"]
    assert not fake.annotations                                   # not on MCP until Facet PR-1: skipped
    assert fake.photos[P1]["tag_created_by"]["orion"] == "astro-id"
    assert mcp_calls(fake, "add_tags") and not any(c[1].startswith("/api/") for c in fake.calls)
    assert not any(name in ("set_rating", "delete_photos") for name, _ in fake.mcp_calls)


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


def test_page_two_sections_terms_and_script_parses(tmp_path):
    """Panel restructure (Ray's review 2026-11-12): A. Setup / B. Run, one term each, min confidence only as a
    sky-label display setting, Save to Facet after the review, culling last and low priority."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    text = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
    for want in ("A. Setup for this folder or album", "B. Run", "Save setup", "Save to Facet", "Sky labels",
                 "Labeled copies", "Show sky labels", "Min confidence", "Culling (low priority)", "<b>tags</b>"):
        assert want in text, want
    assert text.index("A. Setup") < text.index("Plate solve") < text.index("Save to Facet") < text.index("Culling")
    for gone in ("Overlay", "overlay images", "write tags to Facet", 'id="minconf"', 'id="write"'):
        assert gone not in text, gone
    solve_step = text[text.index('id="s4"'):text.index('id="s5"')]
    assert "confidence" not in solve_step.replace("confidence after each name", "")   # min confidence left the solve
    assert "bridge.annotationView(" in html and '"api/save"' in html and "api/annotations?path=" in html
    if NODE:
        script = re.findall(r"<script>(.*?)</script>", html.replace("<!--ASTRO_BRIDGE-->", ""), flags=re.S)[-1]
        f = tmp_path / "page.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr


def test_lifeline_watches_the_chain_through_uv_and_facet():
    from astro_id.facet.lifeline import ancestors_to_watch

    # python(10) <- venv launcher(9) <- astro-id.exe(8) <- uv.exe(7) <- Facet python(6) <- explorer(5)
    table = {10: (9, "python.exe"), 9: (8, "python.exe"), 8: (7, "astro-id.exe"), 7: (6, "uv.exe"),
             6: (5, "python.exe"), 5: (1, "explorer.exe")}
    assert ancestors_to_watch(10, table) == [9, 8, 7, 6]
    # no uv in the chain (run by hand): just the parent
    assert ancestors_to_watch(10, {10: (9, "python.exe"), 9: (4, "cmd.exe"), 4: (0, "x")}) == [9]
    assert ancestors_to_watch(10, {10: (99, "python.exe")}) == []          # parent already gone


def test_album_picker_lists_only_fixed_albums():
    """A smart album is a saved filter: Facet's album_id scope sees only fixed members, so it is left out."""
    from astro_id.facet.serve import App

    class C:
        def albums(self):
            return [{"id": 4, "name": "Astro", "is_smart": 1, "photo_count": 0},
                    {"id": 6, "name": "Astro test", "is_smart": 0, "photo_count": 18}]

    app = App.__new__(App)
    app.client = C()
    assert App.albums(app, {}, {}) == {"albums": [{"id": 6, "name": "Astro test", "count": 18}], "smart_skipped": 1}
