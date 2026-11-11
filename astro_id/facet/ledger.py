"""Local ledger: what astro-id wrote into a Facet library, so a re-run removes only its own tags.

Facet's ``POST /api/photos/batch_manual_tags`` stores tags as ordinary user tags (``source='user'``); nothing on
the Facet side says astro-id wrote them. The ledger is that record, kept on the sidecar's machine:

- ``photos``: one row per (Facet URL, photo path): the tags astro-id added, the hash of the annotation payload it
  stored and how (``api`` / ``legacy-db`` / none), the Facet version, the last solve status, when.
- ``meta``: per Facet URL key/value pairs (the ``--since last`` watermark).

Location: ``--ledger`` > ``$ASTRO_ID_LEDGER`` > ``%LOCALAPPDATA%\\astro-id\\facet-ledger.sqlite`` (Windows) or
``$XDG_DATA_HOME/astro-id/facet-ledger.sqlite`` (default ``~/.local/share``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

LEDGER_NAME = "facet-ledger.sqlite"
SCHEMA_VERSION = 1


def data_dir() -> Path:
    """Platform data folder for astro-id (no files created here)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "astro-id"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "astro-id"


def default_ledger_path() -> Path:
    env = os.environ.get("ASTRO_ID_LEDGER")
    return Path(env) if env else data_dir() / LEDGER_NAME


def library_key(facet_url: str) -> str:
    """One Facet library = one base URL (scheme://host:port, lowercased, no trailing slash)."""
    return facet_url.strip().rstrip("/").lower()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _locked(fn):
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Ledger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the web UI's request threads and its solve thread, serialised by a lock.
        self.conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self.lock = threading.RLock()
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS photos (
                library TEXT NOT NULL,
                path TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                payload_hash TEXT,
                annotation_via TEXT,
                status TEXT,
                facet_version TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (library, path)
            );
            CREATE TABLE IF NOT EXISTS meta (
                library TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (library, key)
            );
            """)
        self.conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -------------------------------------------------------------- photos ---
    @_locked
    def get(self, library: str, path: str) -> dict | None:
        row = self.conn.execute(
            "SELECT tags, payload_hash, annotation_via, status, facet_version, updated_at FROM photos "
            "WHERE library = ? AND path = ?", (library, path)).fetchone()
        if row is None:
            return None
        return {"path": path, "tags": json.loads(row[0] or "[]"), "payload_hash": row[1], "annotation_via": row[2],
                "status": row[3], "facet_version": row[4], "updated_at": row[5]}

    def tags(self, library: str, path: str) -> list[str]:
        r = self.get(library, path)
        return list(r["tags"]) if r else []

    @_locked
    def known_paths(self, library: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT path FROM photos WHERE library = ?", (library,))}

    @_locked
    def record(self, library: str, path: str, *, tags: list[str], payload_hash: str | None,
               annotation_via: str | None, status: str | None, facet_version: str | None) -> None:
        self.conn.execute(
            "INSERT INTO photos (library, path, tags, payload_hash, annotation_via, status, facet_version, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(library, path) DO UPDATE SET tags = excluded.tags, "
            "payload_hash = excluded.payload_hash, annotation_via = excluded.annotation_via, status = excluded.status, "
            "facet_version = excluded.facet_version, updated_at = excluded.updated_at",
            (library, path, json.dumps(sorted(set(tags))), payload_hash, annotation_via, status, facet_version, _now()))
        self.conn.commit()

    @_locked
    def forget(self, library: str, path: str) -> None:
        self.conn.execute("DELETE FROM photos WHERE library = ? AND path = ?", (library, path))
        self.conn.commit()

    @_locked
    def rows(self, library: str) -> list[dict]:
        return [self.get(library, p) for (p,) in self.conn.execute(
            "SELECT path FROM photos WHERE library = ? ORDER BY path", (library,))]

    # ---------------------------------------------------------------- meta ---
    @_locked
    def get_meta(self, library: str, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE library = ? AND key = ?", (library, key)).fetchone()
        return row[0] if row else None

    @_locked
    def set_meta(self, library: str, key: str, value: str | None) -> None:
        self.conn.execute("INSERT INTO meta (library, key, value) VALUES (?, ?, ?) "
                          "ON CONFLICT(library, key) DO UPDATE SET value = excluded.value", (library, key, value))
        self.conn.commit()
