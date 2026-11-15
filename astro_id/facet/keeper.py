"""The add-on keeper: lets the Astro add-on restart itself on new code without Facet noticing.

Facet's supervisor starts ``uv run ... astro-id facet serve --addon`` once and never restarts it; when that process
exits, Facet closes every Astro panel (their nonces go stale) and only a click on the Add-ons page starts it again.
Meanwhile Facet reads the panel page from disk on every load, so after a code update the page is newer than the
running server.

So in add-on mode the process Facet started is a small **keeper** (stdlib only, this file) and the real server is a
**worker** child:

1. The keeper binds the listening socket (``127.0.0.1``, ``FACET_PORT`` or a free port), prints
   ``FACET_ADDON_PORT=<n>`` and starts the lifeline (it exits when uv or Facet ends).
2. It starts a worker (``sys.executable -c ... facet serve --addon`` with ``ASTRO_ID_WORKER=1``) and hands it the
   same socket: on Windows ``socket.share(pid)`` bytes on the worker's stdin, on POSIX an inherited fd. The worker
   serves exactly as before (same port, same launch secret, same Facet token), so Facet's proxy never sees a change.
3. When the panel asks for an update (``POST api/restart``) the worker writes its session state to the handoff file
   (``ASTRO_ID_HANDOFF``) and exits with :data:`RESTART_EXIT`. The keeper keeps the socket open, so requests that
   arrive meanwhile wait in the listen backlog, and starts a new worker, which imports the code now on disk and
   reads the handoff file.
4. Any other worker exit code ends the keeper with that code (Facet then shows the add-on as exited, as before),
   except right after an update (see 6).

Safe update (B-P10, 2026-11-15: a half-edited checkout took the add-on down):

5. **Never retire a working worker for code that cannot serve.** The worker watches its code on disk
   (:class:`UpdateGate`, a thread). New code is only considered once its ``.py`` files have been unchanged for
   :data:`SETTLE_S` seconds (a multi-file edit or a git checkout is not picked up half-way). Then a throwaway
   **probe** process (:func:`probe`) starts the new code on a private port, and must answer ``/health`` and
   ``GET api/state`` sanely within :data:`PROBE_TIMEOUT` s. Only a fingerprint that passed is offered to the panel
   (``api/state.update.code_changed``) and accepted by ``api/restart``. A fingerprint that failed is remembered and
   not tried again until the files change (``api/state.update.update_failed`` = {reason, at}); the old worker keeps
   serving.
6. **After the swap** the keeper checks the new worker itself (``api/state`` within :data:`VERIFY_TIMEOUT` s, and
   no exit within :data:`CRASH_WINDOW` s). The previous code is gone from disk by then, so it cannot go back;
   instead it stays alive and serves a minimal fallback (:class:`Fallback`: ``/health``, an ``api/state`` that says
   the update did not start, 503 for the rest) on the same socket, restores the session handoff, and probes the
   code again whenever the files change; the first version that passes gets a new worker. Facet's proxy never sees a
   dead add-on and the panel can say what happened.

The keeper's own code is what was on disk when Facet started the add-on; keep it small and stable.
"""
from __future__ import annotations

import base64
import hmac
import http.client
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("astro_id.facet")

RESTART_EXIT = 75                 # worker -> keeper: "start me again on the code now on disk" (EX_TEMPFAIL)
WORKER_ENV = "ASTRO_ID_WORKER"    # set in a worker's environment
HANDOFF_ENV = "ASTRO_ID_HANDOFF"  # the file a worker leaves its session state in for the next one
SOCKET_ENV = "ASTRO_ID_LISTEN"    # how the worker gets the socket: "stdin" (Windows share bytes) or an fd number
NO_KEEPER_ENV = "ASTRO_ID_NO_KEEPER"  # escape hatch: "1" = serve in this process (no self-update)
PROBE_ENV = "ASTRO_ID_PROBE"      # "1" in a probe: private port, no session, no keeper, no update watcher
SETTLE_ENV = "ASTRO_ID_SETTLE"    # tests: seconds the code must stay unchanged (default SETTLE_S)
WATCH_ENV = "ASTRO_ID_WATCH_EVERY"  # tests: seconds between looks at the code on disk (default WATCH_EVERY)
MAX_RESTARTS = 5                  # more updates than this within RESTART_WINDOW s = a loop: stop
RESTART_WINDOW = 60.0
BACKLOG = 64                      # requests that arrive during an update wait here
SETTLE_S = 10.0                   # new code must be unchanged this long before it is tried (debounce)
WATCH_EVERY = 2.0                 # how often the code on disk is looked at
PROBE_TIMEOUT = 20.0              # a probe must start and answer api/state within this
VERIFY_TIMEOUT = 20.0             # after a swap: the new worker must answer api/state within this
CRASH_WINDOW = 30.0               # after a swap: an exit within this (other than RESTART_EXIT) = the update failed
WORKER_CODE = "import sys; from astro_id.cli import main; sys.exit(main(sys.argv[1:]))"
PACKAGE = Path(__file__).resolve().parents[1]    # the astro_id package: its .py files are what a restart reloads
PAGE = Path(__file__).with_name("static") / "index.html"
FALLBACK_ERROR = "Astro's update did not start; Astro will try again when its files change."


def wanted(env=None) -> bool:
    """True in the process Facet started (not in a worker, not a probe, not switched off)."""
    e = os.environ if env is None else env
    return not e.get(WORKER_ENV) and e.get(NO_KEEPER_ENV) != "1" and e.get(PROBE_ENV) != "1"


def should_respawn(code: int | None, history: list[float], now: float) -> bool:
    """The keeper's decision after a worker exited: start another one only for :data:`RESTART_EXIT`, and not more
    than :data:`MAX_RESTARTS` times within :data:`RESTART_WINDOW` seconds (``history`` holds the earlier restart
    times and is updated)."""
    if code != RESTART_EXIT:
        return False
    history[:] = [t for t in history if now - t < RESTART_WINDOW]
    if len(history) >= MAX_RESTARTS:
        return False
    history.append(now)
    return True


def _seconds(env, name: str, default: float) -> float:
    try:
        v = float((env or {}).get(name) or default)
    except ValueError:
        return default
    return v if v >= 0 else default


def settle_seconds(env=None) -> float:
    return _seconds(os.environ if env is None else env, SETTLE_ENV, SETTLE_S)


def watch_seconds(env=None) -> float:
    return max(0.05, _seconds(os.environ if env is None else env, WATCH_ENV, WATCH_EVERY))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ the code on disk ---

def code_stamp(root: Path = PACKAGE) -> str:
    """A fingerprint of the add-on's Python code on disk (every ``.py`` path, size and mtime): it changes when any
    of it is edited, added or removed. The page and bridge are read from disk per request, so they are not in it."""
    h = sha256()
    for p in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.relative_to(root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


def disk_page_version(path: Path | None = None) -> str | None:
    """The PAGE_VERSION of the page on disk now (what a panel opened or reloaded now gets), or None."""
    try:
        text = (path or PAGE).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'const PAGE_VERSION = "([^"]+)";', text)
    return m.group(1) if m else None


class UpdateGate:
    """Which code on disk may replace the running code. :meth:`tick` (a watcher thread, every few seconds) looks at
    the fingerprint; a fingerprint unchanged for ``settle`` seconds is probed once (``probe_fn() -> {ok, reason,
    stamp}``); the verdict is kept per fingerprint, so a bad version is not tried again until the files change, and
    a version that changed during its probe gets no verdict (it settles again).

    :meth:`status` is what api/state reports: ``ready`` (the code on disk passed: offer the update), ``pending``
    (it differs and is settling or being probed), ``failed`` ({reason, at} for the code on disk) or all clear."""

    def __init__(self, running: str | None, probe_fn, stamp_fn=code_stamp, clock=time.monotonic,
                 settle: float = SETTLE_S):
        self.running = running
        self.probe_fn, self.stamp_fn, self.clock, self.settle = probe_fn, stamp_fn, clock, settle
        self.ready: str | None = None
        self.failed: dict[str, dict] = {}
        self.probes = 0                       # how many probes ran (tests: no retry loop)
        self._seen: tuple[str | None, float] = (None, 0.0)
        self._lock = threading.Lock()

    def tick(self) -> None:
        stamp = self.stamp_fn()
        now = self.clock()
        with self._lock:
            if stamp != self._seen[0]:
                self._seen = (stamp, now)
            if stamp in (self.running, self.ready) or stamp in self.failed:
                return
            if now - self._seen[1] < self.settle:
                return
        self.probes += 1
        try:
            r = self.probe_fn() or {}
        except Exception as exc:   # a probe that cannot even run is a failed probe
            r = {"ok": False, "reason": f"probe could not run: {exc}"}
        after = self.stamp_fn()
        with self._lock:
            if after != stamp or (r.get("stamp") and r["stamp"] != stamp):
                self._seen = (after, self.clock())      # edited during the probe: no verdict, settle again
                log.info("self-update: code changed while it was being tried; waiting for it to settle")
                return
            if r.get("ok"):
                self.ready = stamp
                log.info("self-update: the new code started and answered; the update is ready")
            else:
                self.failed[stamp] = {"reason": str(r.get("reason") or "did not start")[:300], "at": now_iso()}
                log.warning("self-update: probe failed, keeping the running version: %s", self.failed[stamp]["reason"])

    def status(self, stamp: str | None = None) -> dict:
        stamp = self.stamp_fn() if stamp is None else stamp
        with self._lock:
            if stamp == self.running:
                return {"ready": False, "pending": False, "failed": None}
            if stamp == self.ready:
                return {"ready": True, "pending": False, "failed": None}
            if stamp in self.failed:
                return {"ready": False, "pending": False, "failed": dict(self.failed[stamp])}
            return {"ready": False, "pending": True, "failed": None}

    def run_forever(self, every: float = WATCH_EVERY, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:   # never let the watcher die quietly in a loop of errors
                log.exception("self-update: watcher")
            stop.wait(every)


# ------------------------------------------------------------------------- probe ---

def health_ack(secret: str, challenge: str) -> str:
    return hmac.new(secret.encode("utf-8"), challenge.encode("utf-8"), sha256).hexdigest()


def _get(port: int, path: str, secret: str | None, timeout: float):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=max(0.5, timeout))
    try:
        c.request("GET", path, headers={"X-Facet-Launch": secret} if secret else {})
        r = c.getresponse()
        data = r.read()
        return r.status, data
    finally:
        c.close()


def check_state(status: int, data: bytes) -> str | None:
    """None when an api/state answer is sane; else why not."""
    if status != 200:
        try:
            err = json.loads(data).get("error")
        except (ValueError, AttributeError):
            err = None
        return f"api/state answered HTTP {status}" + (f": {err}" if err else "")
    try:
        st = json.loads(data)
    except ValueError:
        return "api/state did not answer JSON"
    u = st.get("update") if isinstance(st, dict) else None
    if not (isinstance(u, dict) and isinstance(u.get("boot"), str) and isinstance(st.get("page_version"), str)):
        return "api/state answered without its update/page_version fields"
    return None


def _tail(path: str, n: int = 300) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # the exception line of a traceback, else the last line
    for ln in reversed(lines):
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Interrupt|Exit)\b", ln):
            return ln[:n]
    return lines[-1][:n] if lines else ""


def probe(argv: list[str], env: dict, timeout: float = PROBE_TIMEOUT, cmd: list[str] | None = None,
          popen=subprocess.Popen) -> dict:
    """Start the code now on disk as a throwaway add-on on a private port and check it can import, start and answer
    ``/health`` and ``GET /ui/api/state`` sanely. {ok, reason, stamp (the fingerprint it started on), seconds}.
    The probe gets the same launch secret and Facet credentials as a worker, no handoff file and no shared socket;
    it is killed when the check is done (a probe's lifeline also ends it with its parent: a stdin-reading thread
    was tried and stalls the server on Windows)."""
    e = {k: v for k, v in env.items() if k not in (WORKER_ENV, SOCKET_ENV, HANDOFF_ENV)}
    e.update({NO_KEEPER_ENV: "1", PROBE_ENV: "1", "FACET_PORT": ""})
    secret = e.get("FACET_LAUNCH_SECRET") or ""
    t0 = time.monotonic()
    deadline = t0 + timeout
    fd, errpath = tempfile.mkstemp(prefix="astro-id-probe-", suffix=".log")
    errf = os.fdopen(fd, "w", encoding="utf-8")
    p = None
    result: dict = {"ok": False, "reason": "did not start", "stamp": None}
    try:
        p = popen(cmd or worker_argv(argv), env=e, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf)
        got: list[bytes] = []
        ev = threading.Event()

        def read_port():
            try:
                got.append(p.stdout.readline())
            except (OSError, ValueError):
                pass
            ev.set()

        threading.Thread(target=read_port, name="astro-probe-port", daemon=True).start()
        while not ev.wait(0.05):
            if time.monotonic() >= deadline:
                result["reason"] = f"did not start within {timeout:.0f} s"
                return result
            if p.poll() is not None and not ev.wait(0.5):
                break
        m = re.fullmatch(rb"FACET_ADDON_PORT=(\d+)\s*", got[0] if got else b"")
        if not m:
            p.wait(timeout=max(0.1, min(5.0, deadline - time.monotonic())))
            errf.flush()
            why = _tail(errpath)
            result["reason"] = f"did not start (exit code {p.poll()})" + (f": {why}" if why else "")
            return result
        port = int(m.group(1))
        c = secrets.token_hex(8)
        last = "did not answer"
        healthy = False
        fails = 0
        while time.monotonic() < deadline:
            if p.poll() is not None:
                errf.flush()
                why = _tail(errpath)
                result["reason"] = f"stopped on start-up (exit code {p.returncode})" + (f": {why}" if why else "")
                return result
            left = deadline - time.monotonic()
            try:
                if not healthy:
                    status, data = _get(port, f"/health?c={c}", None, min(5.0, left))
                    if status != 200 or json.loads(data).get("ack") != health_ack(secret, c):
                        result["reason"] = f"/health did not answer the launch challenge (HTTP {status})"
                        return result
                    healthy = True
                status, data = _get(port, "/ui/api/state", secret, min(10.0, left))
            except (OSError, ValueError, http.client.HTTPException) as exc:
                last = f"did not answer: {exc}"
                time.sleep(0.1)
                continue
            if status == 503:                       # still connecting to Facet ("starting")
                last = "still starting"
                time.sleep(0.1)
                continue
            why = check_state(status, data)
            if why is None:
                st = json.loads(data)
                result.update(ok=True, reason="", stamp=st["update"].get("stamp"))
                return result
            last = why
            fails += 1
            if fails >= 3:                          # the same wrong answer three times: that is its answer
                break
            time.sleep(0.2)
        result["reason"] = last if last != "did not answer" else f"did not answer within {timeout:.0f} s"
        return result
    except OSError as exc:
        result["reason"] = f"could not be started: {exc}"
        return result
    finally:
        result["seconds"] = round(time.monotonic() - t0, 2)
        if p is not None:
            _stop(p)
        errf.close()
        try:
            os.unlink(errpath)
        except OSError:
            pass


def _stop(p, grace: float = 5.0) -> None:
    try:
        p.kill()
        p.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if p.stdout:
            p.stdout.close()
    except OSError:
        pass


# ------------------------------------------------------------------ socket + worker ---

def listen_socket(port: int = 0) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(BACKLOG)
    except OSError:
        s.close()
        raise
    return s


def worker_argv(argv: list[str]) -> list[str]:
    return [sys.executable, "-c", WORKER_CODE, *argv]


def spawn_worker(sock: socket.socket, argv: list[str], env: dict, popen=subprocess.Popen):
    """Start a worker that serves on ``sock``."""
    env = dict(env)
    env[WORKER_ENV] = "1"
    if sys.platform == "win32":
        env[SOCKET_ENV] = "stdin"
        p = popen(worker_argv(argv), env=env, stdin=subprocess.PIPE)
        try:
            p.stdin.write(base64.b64encode(sock.share(p.pid)) + b"\n")
            p.stdin.close()
        except OSError:
            p.kill()
            raise
        return p
    env[SOCKET_ENV] = str(sock.fileno())
    return popen(worker_argv(argv), env=env, stdin=subprocess.DEVNULL, pass_fds=(sock.fileno(),))


def inherited_socket(env=None, stdin=None) -> socket.socket | None:
    """In a worker: the keeper's listening socket, or None when this process is not a worker."""
    e = os.environ if env is None else env
    how = e.get(SOCKET_ENV)
    if not e.get(WORKER_ENV) or not how:
        return None
    if how == "stdin":
        line = (stdin or sys.stdin.buffer).readline()
        return socket.fromshare(base64.b64decode(line.strip()))
    return socket.socket(fileno=int(how))


def handoff_path(env=None) -> Path | None:
    e = os.environ if env is None else env
    v = e.get(HANDOFF_ENV)
    return Path(v) if v else None


# ----------------------------------------------------------------------- fallback ---

class Fallback:
    """What the keeper serves on the shared socket while no worker can: ``/health`` (so Facet's proxy still has a
    live add-on), ``GET /ui/api/state`` saying the update did not start (``update.fallback``, ``update_failed``,
    ``pending`` while new code is being tried), and 503 with one sentence for everything else."""

    def __init__(self, sock: socket.socket, secret: str, addon_id: str, failure: dict, gate: UpdateGate | None = None):
        self.sock, self.secret, self.addon_id, self.failure, self.gate = sock, secret, addon_id, failure, gate
        self.boot = "fallback-" + secrets.token_hex(4)
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def state(self) -> dict:
        s = self.gate.status() if self.gate else {"ready": False, "pending": False, "failed": None}
        failed = s["failed"] or self.failure
        page = disk_page_version()
        return {"addon": self.addon_id, "page_version": page or "", "warnings": [FALLBACK_ERROR],
                "loaded": None, "fallback": True,
                "update": {"supported": True, "fallback": True, "boot": self.boot, "code_changed": False,
                           "pending": bool(s["pending"]),
                           "update_failed": {"reason": failed.get("reason"), "at": failed.get("at")},
                           "busy": None, "restarting": False, "unsaved": False, "disk_page_version": page,
                           "stamp": None}}

    def handler(self):
        fb = self

        class H(BaseHTTPRequestHandler):
            timeout = 5            # HTTP/1.0, one request per connection: a slow client cannot hold the keeper

            def log_message(self, fmt, *args):   # quiet: stderr is Facet's add-on log
                pass

            def _send(self, code: int, doc) -> None:
                body = json.dumps(doc).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _route(self):
                u = urlparse(self.path)
                if u.path == "/health" and self.command in ("GET", "HEAD"):
                    c = (parse_qs(u.query).get("c") or [""])[0]
                    if not 1 <= len(c) <= 512:
                        return self._send(400, {"error": "challenge c required"})
                    return self._send(200, {"addon": fb.addon_id, "ack": health_ack(fb.secret, c)})
                got = self.headers.get("X-Facet-Launch") or ""
                if not hmac.compare_digest(got.encode("utf-8"), fb.secret.encode("utf-8")):
                    return self._send(403, {"error": "forbidden"})
                if self.command == "POST":   # drain the body so the connection stays sane
                    n = int(self.headers.get("Content-Length") or 0)
                    if n:
                        self.rfile.read(n)
                if u.path == "/ui/api/state" and self.command in ("GET", "HEAD"):
                    return self._send(200, fb.state())
                return self._send(503, {"error": FALLBACK_ERROR})

            do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _route

        return H

    def start(self) -> None:
        srv = HTTPServer(("127.0.0.1", 0), self.handler(), bind_and_activate=False)
        srv.socket.close()
        srv.socket = self.sock
        srv.server_address = self.sock.getsockname()
        srv.server_close = lambda: None      # the socket is the keeper's: never closed here
        self.server = srv
        self.thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                                       name="astro-fallback", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server = None


# ------------------------------------------------------------------------- keeper ---

class Keeper:
    """The keeper's loop (see the module docstring). Split into methods so tests can replace ``spawn``,
    ``verify``, ``probe_fn`` and the clock."""

    def __init__(self, sock: socket.socket, argv: list[str], env: dict, handoff: Path | None,
                 popen=subprocess.Popen, probe_fn=None, stamp_fn=code_stamp, clock=time.monotonic,
                 settle: float | None = None, watch_every: float | None = None,
                 verify_timeout: float = VERIFY_TIMEOUT, crash_window: float = CRASH_WINDOW):
        self.sock, self.argv, self.env, self.handoff, self.popen = sock, argv, env, handoff, popen
        self.port = sock.getsockname()[1]
        self.secret = env.get("FACET_LAUNCH_SECRET") or ""
        self.addon_id = env.get("FACET_ADDON_ID") or "astro-id"
        self.probe_fn = probe_fn or (lambda: probe(self.argv, self.env))
        self.stamp_fn, self.clock = stamp_fn, clock
        self.settle = settle_seconds(env) if settle is None else settle
        self.watch_every = watch_seconds(env) if watch_every is None else watch_every
        self.verify_timeout, self.crash_window = verify_timeout, crash_window
        self.fallback: Fallback | None = None
        self.fallbacks = 0
        self._stop = threading.Event()

    # -- the session handoff survives a failed swap
    def _bak(self) -> Path | None:
        return self.handoff.with_name(self.handoff.name + ".bak") if self.handoff else None

    def backup_handoff(self) -> None:
        bak = self._bak()
        if bak is None:
            return
        try:
            if self.handoff.is_file():
                shutil.copyfile(self.handoff, bak)
            else:
                bak.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("keeper: could not keep a copy of the session: %s", exc)

    def restore_handoff(self) -> None:
        bak = self._bak()
        try:
            if bak is not None and bak.is_file() and not self.handoff.exists():
                os.replace(bak, self.handoff)
                log.info("keeper: the session is kept for the next worker")
        except OSError as exc:
            log.warning("keeper: could not put the session back: %s", exc)

    def drop_backup(self) -> None:
        bak = self._bak()
        try:
            if bak is not None:
                bak.unlink(missing_ok=True)
        except OSError:
            pass

    # -- workers
    def spawn(self):
        return spawn_worker(self.sock, self.argv, self.env, self.popen)

    def verify(self, p) -> str | None:
        """After a swap: None once the new worker answers api/state sanely; else why not (it exited, it answers
        wrongly, or it did not answer within ``verify_timeout``)."""
        end = self.clock() + self.verify_timeout
        last, fails = "did not answer", 0
        while True:
            rc = p.poll()
            if rc is not None:
                return f"the new version stopped on start-up (exit code {rc})"
            left = end - self.clock()
            if left <= 0:
                return f"the new version {last} within {self.verify_timeout:.0f} s"
            try:
                status, data = _get(self.port, "/ui/api/state", self.secret, min(5.0, left))
            except (OSError, http.client.HTTPException):
                time.sleep(0.1)
                continue
            if status == 503:
                last = "was still starting"
                time.sleep(0.1)
                continue
            why = check_state(status, data)
            if why is None:
                return None
            last, fails = why, fails + 1
            if fails >= 3:
                return f"the new version: {why}"
            time.sleep(0.2)

    def wait_exit(self, p, seconds: float):
        """The worker's exit code if it exits within ``seconds``, else None (still running)."""
        if seconds <= 0:
            return p.poll()
        try:
            return p.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return None

    def kill(self, p) -> None:
        try:
            p.kill()
            p.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def fall_back(self, why: str):
        """No worker can serve: serve the fallback on the socket until a new version passes the probe; then the
        new worker (or None when stopped)."""
        failure = {"reason": why[:300], "at": now_iso()}
        stamp = self.stamp_fn()
        log.warning("self-update: %s; Astro keeps answering and tries again when its files change", why)
        self.restore_handoff()
        gate = UpdateGate(None, self.probe_fn, self.stamp_fn, self.clock, self.settle)
        gate.failed[stamp] = failure
        self.fallbacks += 1
        self.fallback = Fallback(self.sock, self.secret, self.addon_id, failure, gate)
        self.fallback.start()
        try:
            while not self._stop.is_set():
                gate.tick()
                if gate.ready:
                    break
                self._stop.wait(self.watch_every)
            if self._stop.is_set():
                return None
        finally:
            self.fallback.stop()
            self.fallback = None
        log.info("self-update: a fixed version started in a probe; starting it")
        self.backup_handoff()
        return self.spawn()

    def run(self) -> int:
        history: list[float] = []
        swapped: float | None = None          # when the running worker replaced another (None: Facet's launch)
        p = self.spawn()
        try:
            while True:
                why = None
                if swapped is not None:
                    why = self.verify(p)
                    if why is None:
                        code = self.wait_exit(p, swapped + self.crash_window - self.clock())
                        if code is None:
                            self.drop_backup()        # it came up and stayed up: the update is done
                            code = p.wait()
                        elif code != RESTART_EXIT:
                            why = f"the new version stopped (exit code {code}) right after the update"
                    else:
                        self.kill(p)
                else:
                    code = p.wait()
                if why is not None:
                    p = self.fall_back(why)
                    if p is None:
                        return 0
                    swapped = self.clock()
                    continue
                if not should_respawn(code, history, time.monotonic()):
                    if code == RESTART_EXIT:
                        log.warning("astro-id: %d updates within %ds; stopping", MAX_RESTARTS, int(RESTART_WINDOW))
                    return code
                log.info("keeper: starting a new worker (same port, same launch secret)")
                self.backup_handoff()
                p = self.spawn()
                swapped = self.clock()
        except KeyboardInterrupt:
            if p is not None:
                p.terminate()
            return 0
        finally:
            self._stop.set()
            self.drop_backup()


def run_keeper(argv: list[str], env=None, out=None, lifeline: bool = True, popen=subprocess.Popen) -> int:
    e = dict(os.environ if env is None else env)
    out = out or sys.stdout
    port = (e.get("FACET_PORT") or "").strip()
    if port and not port.isdigit():
        print("FACET_PORT must be a port number", file=sys.stderr)
        return 2
    sock = listen_socket(int(port) if port else 0)
    out.write(f"FACET_ADDON_PORT={sock.getsockname()[1]}\n")
    out.flush()
    if lifeline:
        from .lifeline import watch

        log.info("lifeline: exits when any of %s ends", watch() or "nothing (not watched)")
    handoff = Path(tempfile.gettempdir()) / f"astro-id-handoff-{os.getpid()}.json"
    e[HANDOFF_ENV] = str(handoff)
    try:
        handoff.unlink(missing_ok=True)      # a fresh launch starts clean (a reused PID's leftover)
    except OSError:
        pass
    try:
        return Keeper(sock, argv, e, handoff, popen).run()
    finally:
        sock.close()
        try:
            handoff.unlink(missing_ok=True)
        except OSError:
            pass
