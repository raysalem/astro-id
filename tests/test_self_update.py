"""B-P8: the add-on restarts itself on new code (keeper.py + serve.py api/restart), no Stop/Open for the user.

Covers the restart decision, the busy deferral (a solve or an export is never cut), the session handoff (an unsaved
solve survives the restart), the keeper's respawn rule, and one real restart of a keeper-run add-on process: same
port, same launch secret, same keeper process, new worker with a new boot id and the session restored."""
from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from astro_id.facet import keeper as K
from astro_id.facet import serve as SV
from fake_facet import FakeFacet

SECRET = "launch-secret-0123456789abcdef"
TOKEN = "fct_addon_token_for_tests"
REPO = Path(__file__).resolve().parents[1]
P1 = "//nas/Photos/night/A1.JPG"


# ------------------------------------------------------------ pure decisions ---

def test_restart_decision():
    assert SV.restart_decision(False, True, None) == {"restart": False, "reason": "unsupported"}
    assert SV.restart_decision(True, True, "solve") == {"restart": False, "reason": "busy", "busy": "solve"}
    assert SV.restart_decision(True, True, "export") == {"restart": False, "reason": "busy", "busy": "export"}
    assert SV.restart_decision(True, False, None) == {"restart": False, "reason": "current"}
    assert SV.restart_decision(True, False, None, force=True) == {"restart": True}
    assert SV.restart_decision(True, True, None) == {"restart": True}
    # busy wins over force: a running solve is never cut
    assert SV.restart_decision(True, True, "solve", force=True)["restart"] is False


def test_keeper_respawns_only_on_the_restart_code_and_not_in_a_loop():
    h: list[float] = []
    assert K.should_respawn(0, h, 0.0) is False            # a normal exit ends the keeper
    assert K.should_respawn(1, h, 0.0) is False            # so does a crash (Facet shows "exited")
    assert all(K.should_respawn(K.RESTART_EXIT, h, float(i)) for i in range(K.MAX_RESTARTS))
    assert K.should_respawn(K.RESTART_EXIT, h, 10.0) is False          # 6th within the window: a loop
    assert K.should_respawn(K.RESTART_EXIT, h, 10.0 + K.RESTART_WINDOW) is True   # later again fine


def test_keeper_wanted_only_in_the_process_facet_started():
    assert K.wanted({}) is True
    assert K.wanted({K.WORKER_ENV: "1"}) is False
    assert K.wanted({K.NO_KEEPER_ENV: "1"}) is False


def test_code_stamp_changes_when_a_py_file_changes(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    s0 = SV.code_stamp(tmp_path)
    (tmp_path / "data.txt").write_text("not code")
    assert SV.code_stamp(tmp_path) == s0
    (tmp_path / "a.py").write_text("x = 22\n")
    assert SV.code_stamp(tmp_path) != s0
    u = SV.SelfUpdate(True, root=tmp_path)
    assert u.code_changed() is False
    (tmp_path / "b.py").write_text("")
    assert u.code_changed() is True


def test_disk_page_version_matches_the_server():
    assert SV.disk_page_version() == SV.PAGE_VERSION


# ------------------------------------------------------- the route, in-process ---

class _Client:
    url = "http://facet.test"
    can_login = True
    mode = "api"


class _Ctx:
    version = "test"
    mode = None
    warnings: list = []
    tags_via = "api"
    model_d = False
    library = "lib"

    def refresh(self):
        return self


class _Ledger:
    path = "ledger.sqlite"

    def get_meta(self, *_a):
        return None


def make_app(tmp_path, changed=True):
    app = SV.App(_Client(), _Ledger(), _Ctx(), None, addon_id="astro-id")
    app.update = SV.SelfUpdate(True, tmp_path / "handoff.json")
    app.update.code_changed = lambda: changed
    app.exits = []
    app.update.exit = lambda: app.exits.append(1)
    return app


def _wait(pred, s=3.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_state_reports_the_update_fields(tmp_path):
    app = make_app(tmp_path)
    u = app.state({}, {})["update"]
    assert u["supported"] is True and u["code_changed"] is True and u["busy"] is None
    assert u["boot"] == app.update.boot and u["disk_page_version"] == SV.PAGE_VERSION and u["unsaved"] is False
    # standalone / no keeper: not supported, never "changed"
    plain = SV.App(_Client(), _Ledger(), _Ctx(), None)
    assert plain.state({}, {})["update"]["supported"] is False
    assert plain.state({}, {})["update"]["code_changed"] is False
    assert plain.restart({}, {}) == {"restart": False, "reason": "unsupported"}


def test_restart_waits_for_a_running_solve_and_export(tmp_path):
    app = make_app(tmp_path)
    app.job.running = True
    assert app.restart({}, {}) == {"restart": False, "reason": "busy", "busy": "solve"}
    assert app.state({}, {})["update"]["busy"] == "solve"
    app.job.running = False
    app.export_job = {"running": True}
    assert app.restart({}, {}) == {"restart": False, "reason": "busy", "busy": "export"}
    app.export_job = {"running": False, "done": 3}
    assert not app.exits and not app.restarting
    r = app.restart({}, {})
    assert r == {"restart": True, "boot": app.update.boot}
    assert _wait(lambda: app.exits == [1])                  # exits after the answer went out
    # once accepted, no new solve or export can start into the restart
    with pytest.raises(ValueError, match="updating"):
        app.export({}, {"paths": [P1]})


def test_solve_refused_while_restarting(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    monkeypatch.setattr(SV.App, "session_opts", lambda self, b, paths: type("O", (), {"min_confidence": 65})())
    app.restarting = True
    with pytest.raises(ValueError, match="updating"):
        app.solve({}, {"paths": [P1]})
    assert app.job.running is False


def test_restart_refused_when_no_code_changed(tmp_path):
    app = make_app(tmp_path, changed=False)
    assert app.restart({}, {}) == {"restart": False, "reason": "current"}
    assert not app.exits


def test_unsaved_results_survive_the_restart(tmp_path):
    """A finished, unsaved solve is written to the handoff file and the next worker takes it over."""
    app = make_app(tmp_path)
    app.photos = {P1: {"path": P1, "filename": "A1.JPG"}}
    app.mount = "/addons/astro-id"
    job = app.job
    job.events = [{"type": "start"}, {"type": "frame", "path": P1}]
    job.results = {P1: {"solved": True, "grade": "A", "ra": np.float64(83.8), "objects": [{"name": "M42"}],
                        "shape": np.array([1, 2])}}
    job.done = {"review": [{"path": P1}], "quality": {P1: {"hfd": np.float32(2.5)}}}
    job.saved_paths = {P1}
    job.min_confidence = 80
    assert app.state({}, {})["update"]["unsaved"] is True
    assert app.restart({}, {})["restart"] is True
    doc = json.loads((tmp_path / "handoff.json").read_text(encoding="utf-8"))
    assert doc["job"]["results"][P1]["ra"] == 83.8 and doc["job"]["results"][P1]["shape"] == [1, 2]

    nxt = SV.App(_Client(), _Ledger(), _Ctx(), None, addon_id="astro-id")
    nxt.update = SV.SelfUpdate(True, tmp_path / "handoff.json")
    assert nxt.load_handoff() is True
    assert not (tmp_path / "handoff.json").exists()        # taken over once
    assert nxt.job.results[P1]["objects"] == [{"name": "M42"}] and nxt.job.saved is False
    assert nxt.job.saved_paths == {P1} and nxt.job.min_confidence == 80 and nxt.job.running is False
    assert nxt.job.snapshot(0)["next"] == 2 and nxt.photos == app.photos and nxt.mount == "/addons/astro-id"
    assert nxt.job.done["quality"][P1]["hfd"] == 2.5
    assert nxt.load_handoff() is False                     # nothing left


def test_unsaved_results_that_cannot_be_kept_block_the_restart(tmp_path):
    app = make_app(tmp_path)
    app.job.results = {P1: {"solved": True, "odd": object()}}
    assert app.restart({}, {}) == {"restart": False, "reason": "unsaved"}
    assert not app.restarting and not app.exits
    # saved results that cannot be written are no loss: the restart goes ahead
    app.job.saved = True
    assert app.restart({}, {})["restart"] is True


def test_a_broken_handoff_file_is_reported_not_silent(tmp_path):
    (tmp_path / "handoff.json").write_text("{not json", encoding="utf-8")
    app = SV.App(_Client(), _Ledger(), _Ctx(), None)
    app.update = SV.SelfUpdate(True, tmp_path / "handoff.json")
    assert app.load_handoff() is False
    assert any("could not bring back" in n for n in app.notes)


# ------------------------------------------------- a real keeper-run process ---

def _req(port, method, path, body=None, timeout=30):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    h = {"X-Facet-Launch": SECRET}
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data and "json" in (r.getheader("Content-Type") or "") else data)


def test_real_restart_keeps_port_secret_and_session(tmp_path):
    """`facet serve --addon` as Facet spawns it: the keeper prints one port line; api/restart (force) brings up a
    new worker on the same port with the same launch secret while the keeper process (the one Facet watches) keeps
    running, and the loaded photo set comes back."""
    with FakeFacet([{"path": P1, "filename": "A1.JPG", "category": "astro", "scan_run": 7}],
                   addon_token=TOKEN) as fake:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FACET_", "ASTRO_ID_"))}
        env.update(FACET_URL=fake.url, FACET_TOKEN=TOKEN, FACET_ADDON_ID="astro-id", FACET_LAUNCH_SECRET=SECRET,
                   ASTRO_ID_LEDGER=str(tmp_path / "l.sqlite"))
        code = "import sys; from astro_id.cli import main; sys.exit(main(sys.argv[1:]))"
        errf = open(tmp_path / "err.log", "w", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-c", code, "facet", "serve", "--addon"], cwd=str(REPO), env=env,
                             stdout=subprocess.PIPE, stderr=errf, text=True)
        try:
            line = p.stdout.readline()
            assert re.fullmatch(r"FACET_ADDON_PORT=\d+\n", line), line
            port = int(line.split("=")[1])
            st = None
            for _ in range(200):
                try:
                    status, st = _req(port, "GET", "/ui/api/state")
                    if status == 200:
                        break
                except OSError:
                    time.sleep(0.05)
            assert st["update"]["supported"] is True and st["update"]["code_changed"] is False
            boot0 = st["update"]["boot"]
            assert _req(port, "POST", "/ui/api/scope", {"kind": "import", "run_id": "7"})[0] == 200
            assert _req(port, "POST", "/ui/api/restart", {})[1] == {"restart": False, "reason": "current"}
            status, r = _req(port, "POST", "/ui/api/restart", {"force": True})
            assert status == 200 and r["restart"] is True
            # requests during the restart wait in the keeper's listen queue: no refused connection
            st2 = None
            for _ in range(100):
                status, st2 = _req(port, "GET", "/ui/api/state")
                if st2["update"]["boot"] != boot0:
                    break
                time.sleep(0.1)
            assert st2["update"]["boot"] != boot0
            assert p.poll() is None                               # the process Facet watches never exited
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/health?c=xyz")
            assert json.loads(c.getresponse().read())["ack"] == hmac.new(SECRET.encode(), b"xyz",
                                                                       sha256).hexdigest()
            c.close()
            # the loaded set came through: its thumbnails are still served
            assert _req(port, "GET", "/ui/api/thumb?path=" + P1)[0] == 200   # 404 = "not in the current set"
        finally:
            p.terminate()
            out, _ = p.communicate(timeout=15)
            errf.close()
        err = (tmp_path / "err.log").read_text(encoding="utf-8")
        assert out == ""                                          # still exactly one stdout line in all
        assert "restarting on the code now on disk" in err, err
        assert "session restored (1 photos" in err, err
