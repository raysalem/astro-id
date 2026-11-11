"""Exit when the process that launched the add-on goes away (spec §5: "the add-on owns its own children").

Facet stops a supervised add-on with terminate -> wait -> kill on the process it spawned, which is ``uv.exe``. On
Windows that does not reach uv's children: the chain is ``uv.exe -> astro-id.exe (trampoline) -> venv python.exe
(launcher) -> python.exe (this server)``, and killing uv leaves the other three running with the port and the
log pipe open (seen 2026-11-11). So at start-up the add-on opens a SYNCHRONIZE handle on each ancestor up to and
including uv's parent (Facet) and exits as soon as any of them ends. Handles pin the process identity, so a reused
PID cannot fool it. On POSIX Facet kills the whole session group (``killpg``); this only polls ``getppid``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("astro_id.facet")
LAUNCHERS = {"uv.exe", "uv"}
MAX_DEPTH = 8


def _win_process_table() -> dict[int, tuple[int, str]]:
    """{pid: (parent pid, exe name)} from a Toolhelp snapshot."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return {}
    out: dict[int, tuple[int, str]] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def ancestors_to_watch(pid: int, table: dict[int, tuple[int, str]]) -> list[int]:
    """The parent chain up to and including the launcher's (uv's) parent; just the parent when there is no uv."""
    chain: list[tuple[int, str]] = []
    cur = pid
    for _ in range(MAX_DEPTH):
        parent = table.get(cur, (0, ""))[0]
        if not parent or parent == cur or parent not in table:
            break
        chain.append((parent, table[parent][1]))
        cur = parent
    for i, (_, name) in enumerate(chain):
        if name in LAUNCHERS:
            return [p for p, _ in chain[:i + 2]]
    return [p for p, _ in chain[:1]]


def _watch_windows(on_exit) -> list[int]:
    import ctypes
    from ctypes import wintypes

    pids = ancestors_to_watch(os.getpid(), _win_process_table())
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    handles, watched = [], []
    for pid in pids:
        h = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if h:
            handles.append(h)
            watched.append(pid)
    if not handles:
        return []
    arr = (wintypes.HANDLE * len(handles))(*handles)

    def wait():
        r = k32.WaitForMultipleObjects(len(handles), arr, False, 0xFFFFFFFF)
        if 0 <= r < len(handles):
            on_exit(watched[r])

    threading.Thread(target=wait, name="astro-lifeline", daemon=True).start()
    return watched


def _watch_posix(on_exit) -> list[int]:
    start = os.getppid()

    def poll():
        while True:
            time.sleep(1.0)
            if os.getppid() != start:
                on_exit(start)
                return

    threading.Thread(target=poll, name="astro-lifeline", daemon=True).start()
    return [start]


def _default_exit(pid: int) -> None:
    log.warning("launcher process %d ended: astro-id add-on exits", pid)
    logging.shutdown()
    os._exit(0)


def watch(on_exit=_default_exit) -> list[int]:
    """Start watching; returns the PIDs watched (empty when nothing could be watched)."""
    try:
        return _watch_windows(on_exit) if sys.platform == "win32" else _watch_posix(on_exit)
    except Exception as exc:   # never stop the add-on from starting
        log.warning("lifeline not started: %s", exc)
        return []
