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
4. Any other worker exit code ends the keeper with that code (Facet then shows the add-on as exited, as before).

The keeper's own code is what was on disk when Facet started the add-on; keep it small and stable.
"""
from __future__ import annotations

import base64
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger("astro_id.facet")

RESTART_EXIT = 75                 # worker -> keeper: "start me again on the code now on disk" (EX_TEMPFAIL)
WORKER_ENV = "ASTRO_ID_WORKER"    # set in a worker's environment
HANDOFF_ENV = "ASTRO_ID_HANDOFF"  # the file a worker leaves its session state in for the next one
SOCKET_ENV = "ASTRO_ID_LISTEN"    # how the worker gets the socket: "stdin" (Windows share bytes) or an fd number
NO_KEEPER_ENV = "ASTRO_ID_NO_KEEPER"  # escape hatch: "1" = serve in this process (no self-update)
MAX_RESTARTS = 5                  # more updates than this within RESTART_WINDOW s = a loop: stop
RESTART_WINDOW = 60.0
BACKLOG = 64                      # requests that arrive during an update wait here
WORKER_CODE = "import sys; from astro_id.cli import main; sys.exit(main(sys.argv[1:]))"


def wanted(env=None) -> bool:
    """True in the process Facet started (not in a worker, not switched off)."""
    e = os.environ if env is None else env
    return not e.get(WORKER_ENV) and e.get(NO_KEEPER_ENV) != "1"


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
    history: list[float] = []
    try:
        handoff.unlink(missing_ok=True)      # a fresh launch starts clean (a reused PID's leftover)
    except OSError:
        pass
    try:
        while True:
            p = spawn_worker(sock, argv, e, popen)
            try:
                code = p.wait()
            except KeyboardInterrupt:
                p.terminate()
                return 0
            if not should_respawn(code, history, time.monotonic()):
                if code == RESTART_EXIT:
                    log.warning("astro-id: %d updates within %ds; stopping", MAX_RESTARTS, int(RESTART_WINDOW))
                return code
            log.info("keeper: starting a new worker (same port, same launch secret)")
    finally:
        sock.close()
        try:
            handoff.unlink(missing_ok=True)
        except OSError:
            pass
