"""B-P10: a self-update never retires a working worker for code that cannot serve (keeper.py, serve.py).

2026-11-15: an agent edited serve.py in the live checkout; the add-on restarted onto half-written code whose api/state
raised on every call, and the panel told Ray to click Stop, then Open. Covered here: the debounce (code must settle),
the probe (a throwaway process on the new code must start and answer api/state), one verdict per code fingerprint
(no retry loop, a retry once the files change), the keeper's check after the swap and its fallback when the new
worker dies anyway, and two real keeper runs on a copy of the package with deliberately broken code."""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from astro_id.facet import keeper as K
from astro_id.facet import serve as SV
from fake_facet import FakeFacet

SECRET = "launch-secret-0123456789abcdef"
TOKEN = "fct_addon_token_for_tests"
REPO = Path(__file__).resolve().parents[1]
P1 = "//nas/Photos/night/A1.JPG"
BAD = {"reason": "api/state answered HTTP 500: AttributeError: x", "at": "2026-11-15T10:00:00"}


# ------------------------------------------------------------- the gate (debounce + verdicts) ---

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def gate(stamps, results, settle=10.0):
    """An UpdateGate on a scripted fingerprint (stamps["now"]) and scripted probe answers."""
    clock = Clock()
    calls = []

    def probe_fn():
        calls.append(stamps["now"])
        r = results.pop(0) if results else {"ok": True}
        if callable(r):
            r = r()
        return r
    g = K.UpdateGate("run", probe_fn, stamp_fn=lambda: stamps["now"], clock=clock, settle=settle)
    return g, clock, calls


def test_gate_waits_until_the_code_has_settled():
    s = {"now": "run"}
    g, clock, calls = gate(s, [])
    g.tick()
    assert g.status() == {"ready": False, "pending": False, "failed": None}     # same code: nothing to do
    s["now"] = "edit1"
    g.tick()
    assert g.status()["pending"] is True and calls == []
    clock.t = 6.0
    s["now"] = "edit2"                       # a second file written 6 s later: the clock starts again
    g.tick()
    clock.t = 15.0
    g.tick()
    assert calls == [], "9 s after the last change: still settling"
    clock.t = 16.5
    g.tick()
    assert calls == ["edit2"], "probed once it was unchanged for 10 s"
    assert g.status() == {"ready": True, "pending": False, "failed": None}


def test_gate_a_failed_version_is_not_tried_again_until_the_files_change():
    s = {"now": "bad"}
    g, clock, calls = gate(s, [{"ok": False, "reason": "did not start (exit code 1): SyntaxError: invalid syntax"},
                               {"ok": True}], settle=0)
    for i in range(20):                      # 20 looks at the same broken code
        clock.t = float(i)
        g.tick()
    assert calls == ["bad"] and g.probes == 1, "one probe per fingerprint: no retry loop"
    st = g.status()
    assert st["ready"] is False and st["pending"] is False
    assert st["failed"]["reason"].startswith("did not start") and re.match(r"\d{4}-\d\d-\d\dT", st["failed"]["at"])
    s["now"] = "fixed"                        # the files changed: tried again
    g.tick()
    assert calls == ["bad", "fixed"] and g.status()["ready"] is True
    s["now"] = "bad"                          # back to the exact broken files: the old verdict stands
    g.tick()
    assert calls == ["bad", "fixed"] and g.status()["failed"] is not None
    s["now"] = "run"                          # reverted to the running code: all clear
    assert g.status() == {"ready": False, "pending": False, "failed": None}


def test_gate_code_edited_during_the_probe_gets_no_verdict():
    s = {"now": "v1"}

    def edited_meanwhile():
        s["now"] = "v2"
        return {"ok": False, "reason": "half-written"}
    g, clock, calls = gate(s, [edited_meanwhile, {"ok": True}], settle=5)
    g.tick()                                  # v1 seen first at 0
    clock.t = 5
    g.tick()                                  # -> probed at 5; v2 written meanwhile
    assert calls == ["v1"] and g.failed == {} and g.status()["pending"] is True
    clock.t = 9
    g.tick()
    assert calls == ["v1"], "v2 must settle first"
    clock.t = 10.5
    g.tick()
    assert calls == ["v1", "v2"] and g.status()["ready"] is True
    # a probe that started on other code than the gate thinks (its own stamp differs): no verdict either
    s2 = {"now": "v3"}
    g2, c2, calls2 = gate(s2, [{"ok": True, "stamp": "other"}], settle=0)
    g2.tick()
    assert calls2 == ["v3"] and g2.ready is None and g2.status()["pending"] is True


def test_gate_a_probe_that_raises_is_a_failed_probe():
    s = {"now": "x"}

    def boom():
        raise OSError("no python")
    g, clock, calls = gate(s, [boom], settle=0)
    g.tick()
    assert "probe could not run" in g.status()["failed"]["reason"]


def test_settle_and_watch_defaults_and_overrides():
    assert K.settle_seconds({}) == K.SETTLE_S == 10.0
    assert K.settle_seconds({K.SETTLE_ENV: "0.5"}) == 0.5
    assert K.settle_seconds({K.SETTLE_ENV: "nonsense"}) == K.SETTLE_S
    assert K.watch_seconds({K.WATCH_ENV: "0"}) == 0.05
    assert K.wanted({K.PROBE_ENV: "1"}) is False             # a probe never starts a keeper of its own


# ------------------------------------------------------------ the server's side (api/state, restart) ---

def test_restart_decision_never_onto_pending_or_failed_code():
    assert SV.restart_decision(True, False, None, pending=True) == {"restart": False, "reason": "pending"}
    assert SV.restart_decision(True, False, None, failed=BAD) == {"restart": False, "reason": "update_failed",
                                                                  "update_failed": BAD}
    assert SV.restart_decision(True, False, None, force=True, failed=BAD)["restart"] is False
    assert SV.restart_decision(True, False, None, force=True, pending=True)["restart"] is False
    assert SV.restart_decision(True, True, "solve", failed=BAD)["reason"] == "busy"
    assert SV.restart_decision(True, True, None) == {"restart": True}


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


class _Gate:
    def __init__(self, st):
        self.st = st

    def status(self):
        return dict(self.st)


def app_with(tmp_path, st):
    app = SV.App(_Client(), _Ledger(), _Ctx(), None, addon_id="astro-id")
    app.update = SV.SelfUpdate(True, tmp_path / "handoff.json")
    app.update.gate = _Gate(st)
    app.exits = []
    app.update.exit = lambda: app.exits.append(1)
    return app


def test_state_and_restart_when_the_new_code_failed_its_probe(tmp_path):
    app = app_with(tmp_path, {"ready": False, "pending": False, "failed": BAD})
    u = app.state({}, {})["update"]
    assert u["code_changed"] is False and u["pending"] is False and u["update_failed"] == BAD
    assert u["fallback"] is False and u["stamp"] == app.update.stamp
    assert app.restart({}, {}) == {"restart": False, "reason": "update_failed", "update_failed": BAD}
    assert app.restart({}, {"force": True})["restart"] is False
    time.sleep(0.4)
    assert app.exits == [] and app.restarting is False, "the working process keeps serving"
    assert not (tmp_path / "handoff.json").exists()


def test_state_and_restart_while_the_new_code_settles(tmp_path):
    app = app_with(tmp_path, {"ready": False, "pending": True, "failed": None})
    u = app.state({}, {})["update"]
    assert u["code_changed"] is False and u["pending"] is True and u["update_failed"] is None
    assert app.restart({}, {}) == {"restart": False, "reason": "pending"}
    app.update.gate.st = {"ready": True, "pending": False, "failed": None}
    assert app.state({}, {})["update"]["code_changed"] is True
    assert app.restart({}, {})["restart"] is True


# ------------------------------------------------------------------------- the probe ---

FAKE_CANDIDATE = r'''
import hashlib, hmac, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
mode = sys.argv[1]
if os.environ.get("ASTRO_ID_WORKER") or "ASTRO_ID_HANDOFF" in os.environ or os.environ.get("ASTRO_ID_PROBE") != "1":
    sys.exit(9)                                     # a probe must never look like a worker or take the session
if mode == "syntax":
    exec("def f(:\n    pass")
if mode == "silent":
    import time; time.sleep(60)
secret = os.environ["FACET_LAUNCH_SECRET"]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/health"):
            c = self.path.split("c=", 1)[1]
            code, body = 200, {"addon": "astro-id", "ack": hmac.new(secret.encode(), c.encode(), hashlib.sha256).hexdigest()}
        elif self.headers.get("X-Facet-Launch") != secret:
            code, body = 403, {"error": "forbidden"}
        elif mode == "500":
            code, body = 500, {"error": "AttributeError: 'App' object has no attribute 'loaded_summary'"}
        elif mode == "nofields":
            code, body = 200, {"facet": "x"}
        else:
            code, body = 200, {"page_version": "2026-11-15.2", "update": {"boot": "b", "stamp": "stamp-ok"}}
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
s = HTTPServer(("127.0.0.1", 0), H)
print(f"FACET_ADDON_PORT={s.server_address[1]}", flush=True)
s.serve_forever()
'''


def run_probe(mode, timeout=15.0):
    procs = []

    def popen(*a, **k):
        p = subprocess.Popen(*a, **k)
        procs.append(p)
        return p
    env = {**os.environ, "FACET_LAUNCH_SECRET": SECRET, K.WORKER_ENV: "1", K.HANDOFF_ENV: "x.json",
           K.SOCKET_ENV: "stdin", "FACET_PORT": "5003"}
    r = K.probe([], env, timeout=timeout, cmd=[sys.executable, "-c", FAKE_CANDIDATE, mode], popen=popen)
    assert procs and procs[0].poll() is not None, "the probe process is gone afterwards"
    return r


def test_probe_passes_for_code_that_starts_and_answers():
    r = run_probe("ok")
    assert r["ok"] is True and r["stamp"] == "stamp-ok" and r["reason"] == ""


def test_probe_fails_for_a_syntax_error_with_the_python_error_as_reason():
    r = run_probe("syntax")
    assert r["ok"] is False
    assert r["reason"].startswith("did not start (exit code 1)") and "SyntaxError" in r["reason"], r


def test_probe_fails_when_api_state_raises():
    r = run_probe("500")
    assert r["ok"] is False and "HTTP 500" in r["reason"] and "loaded_summary" in r["reason"], r


def test_probe_fails_for_an_answer_without_the_update_fields():
    assert "without its update/page_version" in run_probe("nofields")["reason"]


def test_probe_gives_up_at_its_timeout():
    t0 = time.monotonic()
    r = run_probe("silent", timeout=1.5)
    assert r["ok"] is False and "did not start within" in r["reason"]
    assert time.monotonic() - t0 < 10


# --------------------------------------------------------------------- the keeper (unit) ---

class FakeProc:
    """A worker that exits with ``code`` after ``after`` seconds (None: runs until killed)."""

    def __init__(self, code=0, after=0.0):
        self.code, self.after, self.t0, self.killed = code, after, time.monotonic(), False
        self.stdin = None

    def _left(self):
        return None if self.after is None else self.after - (time.monotonic() - self.t0)

    def poll(self):
        if self.killed:
            return -9
        left = self._left()
        return self.code if left is not None and left <= 0 else None

    def wait(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if end is not None and time.monotonic() >= end:
                raise subprocess.TimeoutExpired("w", timeout)
            time.sleep(0.01)

    def kill(self):
        self.killed = True

    def terminate(self):
        self.killed = True


def keeper_with(tmp_path, procs, verdicts, crash_window=0.3):
    sock = K.listen_socket(0)
    k = K.Keeper(sock, [], {"FACET_LAUNCH_SECRET": SECRET}, tmp_path / "handoff.json",
                 probe_fn=lambda: {"ok": True}, crash_window=crash_window, settle=0, watch_every=0.05)
    k.spawned, k.fell, k.verified = [], [], []
    queue = list(procs)

    def spawn():
        p = queue.pop(0)
        p.t0 = time.monotonic()              # its clock starts when the keeper starts it
        k.spawned.append(p)
        return p

    def verify(p):
        k.verified.append(p)
        return verdicts.pop(0) if verdicts else None

    def fall_back(why):
        k.fell.append(why)
        k.restore_handoff()
        return spawn()
    k.spawn, k.verify, k.fall_back = spawn, verify, fall_back
    return k, sock


def test_keeper_a_good_swap_needs_no_fallback(tmp_path):
    w1, w2 = FakeProc(K.RESTART_EXIT, 0.0), FakeProc(0, 0.6)
    k, sock = keeper_with(tmp_path, [w1, w2], [None])
    (tmp_path / "handoff.json").write_text("{}")
    try:
        assert k.run() == 0
    finally:
        sock.close()
    assert k.spawned == [w1, w2] and k.verified == [w2] and k.fell == []
    assert not (tmp_path / "handoff.json.bak").exists(), "the session copy is dropped once the update held"


def test_keeper_new_worker_crashes_right_after_the_swap_falls_back_and_keeps_the_session(tmp_path):
    """Passed its probe, then died within the crash window: the keeper does not exit (Facet would show a dead
    add-on); it falls back, puts the session back for the next worker, and the next good version takes over."""
    w1, w2, w3 = FakeProc(K.RESTART_EXIT, 0.0), FakeProc(3, 0.05), FakeProc(0, 0.6)
    k, sock = keeper_with(tmp_path, [w1, w2, w3], [None, None])
    h = tmp_path / "handoff.json"
    h.write_text('{"photos": {}}')
    real_spawn = k.spawn

    def spawn():
        p = real_spawn()
        if p is w2:
            h.unlink()                       # the new worker took the session over, then died
        return p
    k.spawn = spawn
    try:
        assert k.run() == 0
    finally:
        sock.close()
    assert len(k.fell) == 1 and "exit code 3" in k.fell[0] and "right after the update" in k.fell[0]
    assert k.spawned == [w1, w2, w3]
    assert h.read_text() == '{"photos": {}}', "the session is back for the worker after the fallback"


def test_keeper_new_worker_that_does_not_answer_is_stopped_and_falls_back(tmp_path):
    w1, w2, w3 = FakeProc(K.RESTART_EXIT, 0.0), FakeProc(0, None), FakeProc(0, 0.6)
    k, sock = keeper_with(tmp_path, [w1, w2, w3], ["the new version: api/state answered HTTP 500", None])
    try:
        assert k.run() == 0
    finally:
        sock.close()
    assert w2.killed is True
    assert k.fell == ["the new version: api/state answered HTTP 500"]


def test_keeper_a_crash_long_after_the_update_ends_the_keeper_as_before(tmp_path):
    w1, w2 = FakeProc(K.RESTART_EXIT, 0.0), FakeProc(1, 0.6)
    k, sock = keeper_with(tmp_path, [w1, w2], [None], crash_window=0.2)
    try:
        assert k.run() == 1
    finally:
        sock.close()
    assert k.fell == []


def test_keeper_first_worker_crash_still_ends_the_keeper(tmp_path):
    k, sock = keeper_with(tmp_path, [FakeProc(1, 0.0)], [])
    try:
        assert k.run() == 1
    finally:
        sock.close()
    assert k.verified == [] and k.fell == []


def _get(port, path, secret=SECRET, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"X-Facet-Launch": secret} if secret else {}
    if method == "POST":
        h["Content-Type"] = "application/json"
    c.request(method, path, body=b"{}" if method == "POST" else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data) if data else None


def test_keeper_fallback_answers_on_the_socket_and_hands_over_when_the_files_are_fixed(tmp_path):
    """The real fall_back: the keeper itself answers /health, api/state (fallback + update_failed) and 503 for the
    rest; the same broken files are not probed again; changed files are probed once, a failure keeps the fallback,
    the next good version gets a worker."""
    sock = K.listen_socket(0)
    port = sock.getsockname()[1]
    stamps = {"now": "broken"}
    probes = []
    answers = [{"ok": False, "reason": "did not start (exit code 1): NameError: x"}, {"ok": True}]

    def probe_fn():
        probes.append(stamps["now"])
        return answers.pop(0)
    k = K.Keeper(sock, [], {"FACET_LAUNCH_SECRET": SECRET, "FACET_ADDON_ID": "astro-id"}, tmp_path / "h.json",
                 probe_fn=probe_fn, stamp_fn=lambda: stamps["now"], settle=0, watch_every=0.05)
    (tmp_path / "h.json.bak").write_text('{"photos": {}}')
    worker = FakeProc(0, None)
    k.spawn = lambda: worker
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("p", k.fall_back("the new version stopped (exit code 3) "
                                                                        "right after the update")))
    t.start()
    try:
        assert _wait(lambda: k.fallback is not None and k.fallback.server is not None)
        status, st = _get(port, "/ui/api/state")
        assert status == 200 and st["update"]["fallback"] is True and st["update"]["supported"] is True
        assert "exit code 3" in st["update"]["update_failed"]["reason"] and st["update"]["code_changed"] is False
        assert st["update"]["boot"].startswith("fallback-")
        assert _get(port, "/ui/api/photos", method="POST") == (503, {"error": K.FALLBACK_ERROR})
        assert _get(port, "/ui/api/state", secret="wrong")[0] == 403
        status, h = _get(port, "/health?c=abc", secret=None)
        assert status == 200 and h["ack"] == K.health_ack(SECRET, "abc")
        assert (tmp_path / "h.json").read_text() == '{"photos": {}}', "the session is put back for the next worker"
        time.sleep(0.4)
        assert probes == [], "the files that just failed are not tried again"
        stamps["now"] = "still-broken"
        assert _wait(lambda: probes == ["still-broken"])
        time.sleep(0.3)
        assert probes == ["still-broken"] and t.is_alive(), "one probe per version; the fallback keeps answering"
        assert "NameError" in _get(port, "/ui/api/state")[1]["update"]["update_failed"]["reason"]
        stamps["now"] = "fixed"
        t.join(10)
        assert not t.is_alive() and out["p"] is worker and probes == ["still-broken", "fixed"]
        assert k.fallback is None
    finally:
        k._stop.set()
        t.join(5)
        sock.close()


def test_keeper_handoff_copy_and_restore(tmp_path):
    sock = K.listen_socket(0)
    try:
        k = K.Keeper(sock, [], {}, tmp_path / "h.json")
        (tmp_path / "h.json").write_text("S")
        k.backup_handoff()
        (tmp_path / "h.json").unlink()
        k.restore_handoff()
        assert (tmp_path / "h.json").read_text() == "S"
        (tmp_path / "h.json").write_text("NEWER")      # a newer session is never overwritten by the copy
        k.backup_handoff()
        (tmp_path / "h.json").write_text("NEWEST")
        k.restore_handoff()
        assert (tmp_path / "h.json").read_text() == "NEWEST"
        k.drop_backup()
        assert not (tmp_path / "h.json.bak").exists()
    finally:
        sock.close()


def _wait(pred, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# --------------------------------------------------------- real keeper runs on broken code ---

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


class Addon:
    """`facet serve --addon` as Facet starts it, run from a private copy of the package so its code can be broken
    on purpose (the checkout the tests run from is never touched)."""

    def __init__(self, tmp_path: Path, fake: FakeFacet):
        self.root = tmp_path / "code"
        shutil.copytree(REPO / "astro_id", self.root / "astro_id", ignore=shutil.ignore_patterns("__pycache__"))
        self.serve_py = self.root / "astro_id" / "facet" / "serve.py"
        self.original = self.serve_py.read_text(encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FACET_", "ASTRO_ID_"))}
        env.update(FACET_URL=fake.url, FACET_TOKEN=TOKEN, FACET_ADDON_ID="astro-id", FACET_LAUNCH_SECRET=SECRET,
                   ASTRO_ID_LEDGER=str(tmp_path / "l.sqlite"), PYTHONPATH=str(self.root), PYTHONDONTWRITEBYTECODE="1",
                   **{K.SETTLE_ENV: "0.5", K.WATCH_ENV: "0.2"})
        self.errpath = tmp_path / "err.log"
        self.errf = open(self.errpath, "w", encoding="utf-8")
        code = "import sys; from astro_id.cli import main; sys.exit(main(sys.argv[1:]))"
        self.p = subprocess.Popen([sys.executable, "-c", code, "facet", "serve", "--addon"], cwd=str(self.root),
                                  env=env, stdout=subprocess.PIPE, stderr=self.errf, text=True)
        line = self.p.stdout.readline()
        assert re.fullmatch(r"FACET_ADDON_PORT=\d+\n", line), line
        self.port = int(line.split("=")[1])

    def state(self, until=None, s=60.0):
        end = time.monotonic() + s
        st = None
        while time.monotonic() < end:
            try:
                status, st = _req(self.port, "GET", "/ui/api/state")
                if status == 200 and (until is None or until(st)):
                    return st
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"api/state never matched; last: {st}\n{self.err()}")

    def write(self, text: str) -> None:
        self.serve_py.write_text(text, encoding="utf-8")

    def err(self) -> str:
        if not self.errf.closed:
            self.errf.flush()
        return self.errpath.read_text(encoding="utf-8")

    def close(self):
        self.p.terminate()
        out, _ = self.p.communicate(timeout=15)
        self.errf.close()
        return out


def _broken_state(src: str) -> str:
    """serve.py whose api/state raises AttributeError on every call (the 2026-11-15 incident)."""
    old = "    def state(self, _q, _b):\n        self.ctx.refresh()\n"
    assert old in src
    return src.replace(old, old + "        self.loaded_summary_not_written_yet()\n")


def test_real_broken_update_keeps_the_old_worker_serving(tmp_path):
    """A syntax error, then an api/state that raises: each is probed once, reported as update_failed, refused by
    api/restart, and the running worker keeps answering with its session; once the files are fixed the update goes
    through on its own fingerprint and the session comes along."""
    with FakeFacet([{"path": P1, "filename": "A1.JPG", "category": "astro", "scan_run": 7}],
                   addon_token=TOKEN) as fake:
        a = Addon(tmp_path, fake)
        try:
            st = a.state()
            boot0 = st["update"]["boot"]
            assert st["update"]["code_changed"] is False and st["update"]["update_failed"] is None
            assert _req(a.port, "POST", "/ui/api/scope", {"kind": "import", "run_id": "7"})[0] == 200

            a.write(a.original + "\ndef half_written(:\n")                       # 1. syntax error
            st = a.state(lambda s: s["update"]["update_failed"])
            assert "SyntaxError" in st["update"]["update_failed"]["reason"], st["update"]
            assert st["update"]["code_changed"] is False and st["update"]["boot"] == boot0
            assert _req(a.port, "POST", "/ui/api/restart", {})[1]["reason"] == "update_failed"
            assert _req(a.port, "GET", "/ui/api/thumb?path=" + P1)[0] == 200    # still serving, session intact
            time.sleep(1.5)                                                     # ~7 more looks at the same files
            assert a.err().count("probe failed") == 1, "no retry loop on the same broken files"

            a.write(_broken_state(a.original))                                   # 2. api/state raises
            st = a.state(lambda s: (s["update"]["update_failed"] or {}).get("reason", "").find("HTTP 500") >= 0)
            assert "AttributeError" in st["update"]["update_failed"]["reason"], st["update"]
            assert st["update"]["boot"] == boot0
            assert _req(a.port, "POST", "/ui/api/restart", {"force": True})[1]["restart"] is False
            assert a.err().count("probe failed") == 2

            a.write(a.original + "\n# fixed\n")                                 # 3. fixed: the update goes ahead
            st = a.state(lambda s: s["update"]["code_changed"])
            assert st["update"]["update_failed"] is None and st["update"]["boot"] == boot0
            status, r = _req(a.port, "POST", "/ui/api/restart", {})
            assert status == 200 and r["restart"] is True
            st = a.state(lambda s: s["update"]["boot"] != boot0)
            assert st["update"]["code_changed"] is False and st["update"]["fallback"] is False
            assert _req(a.port, "GET", "/ui/api/thumb?path=" + P1)[0] == 200    # the session came through
            assert a.p.poll() is None
        finally:
            out = a.close()
        assert out == ""                                                         # one stdout line in all
        assert a.err().count("probe failed") == 2


def test_real_crash_right_after_the_swap_falls_back_to_the_keeper(tmp_path):
    """New code that passes its probe but dies as a worker: the keeper stays up, answers api/state itself (fallback,
    update_failed), refuses the rest with one sentence, and starts a worker again when the files are fixed, with the
    session put back."""
    with FakeFacet([{"path": P1, "filename": "A1.JPG", "category": "astro", "scan_run": 7}],
                   addon_token=TOKEN) as fake:
        a = Addon(tmp_path, fake)
        try:
            boot0 = a.state()["update"]["boot"]
            assert _req(a.port, "POST", "/ui/api/scope", {"kind": "import", "run_id": "7"})[0] == 200
            old = "    out = out or sys.stdout\n    sock = keeper.inherited_socket(e, stdin)"
            assert old in a.original
            a.write(a.original.replace(old, "    out = out or sys.stdout\n    if e.get(keeper.WORKER_ENV):\n"
                                            "        raise SystemExit(3)   # test: dies as a worker only\n"
                                            "    sock = keeper.inherited_socket(e, stdin)"))
            a.state(lambda s: s["update"]["code_changed"])                        # the probe passed
            assert _req(a.port, "POST", "/ui/api/restart", {})[1]["restart"] is True
            st = a.state(lambda s: s["update"].get("fallback"), s=40)
            u = st["update"]
            assert u["boot"] != boot0 and "exit code 3" in u["update_failed"]["reason"], u
            assert u["code_changed"] is False and st["warnings"] == [K.FALLBACK_ERROR]
            status, body = _req(a.port, "GET", "/ui/api/thumb?path=" + P1)
            assert status == 503 and body["error"] == K.FALLBACK_ERROR
            assert a.p.poll() is None, "the process Facet watches is still alive"
            time.sleep(1.0)
            assert "fallback" in a.state()["update"]["boot"], "the same files are not started again"

            a.write(a.original + "\n# fixed\n")
            st = a.state(lambda s: not s["update"].get("fallback"), s=60)
            assert st["update"]["boot"] != boot0 and st["update"]["update_failed"] is None
            assert _req(a.port, "GET", "/ui/api/thumb?path=" + P1)[0] == 200    # the session was kept
        finally:
            a.close()
        err = a.err()
        assert "stopped on start-up (exit code 3)" in err and "keeps answering" in err, err
        assert "session restored" in err, err
