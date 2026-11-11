"""TEMPORARY: write annotations straight into a Facet database (``astro-id facet sync --legacy-db PATH``).

Only for a local Facet stack that has the ``photo_annotations`` table but not yet the
``PUT /api/photo/annotations`` route. Remove this module the day that route ships (the sync layer feature-detects
it and prefers it). It uses Facet's own ``db/annotations.py`` (validation + upsert) from a Facet checkout given by
``--facet-dir`` or ``$FACET_DIR``; that file is stdlib-only and is loaded by path, so neither Facet's package nor
its dependencies are imported. Tags never go this way: they always use the HTTP API.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("astro_id.facet")


def load_facet_annotations(facet_dir: str | None):
    d = facet_dir or os.environ.get("FACET_DIR")
    if not d:
        raise ValueError("--legacy-db needs the Facet checkout that has db/annotations.py: --facet-dir or $FACET_DIR")
    f = Path(d) / "db" / "annotations.py"
    if not f.is_file():
        raise ValueError(f"no db/annotations.py in {d} (this Facet has no annotations support)")
    spec = importlib.util.spec_from_file_location("facet_db_annotations", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LegacyAnnotationWriter:
    def __init__(self, db_path: str, facet_dir: str | None = None):
        if not os.path.isfile(db_path):
            raise ValueError(f"--legacy-db: no such database {db_path}")
        self.mod = load_facet_annotations(facet_dir)
        # Used from the web UI's solve thread as well as the main thread: one connection, serialised by a lock.
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.lock = threading.Lock()
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                                 "AND name = 'photo_annotations'").fetchone():
            self.conn.close()
            raise ValueError(f"--legacy-db: {db_path} has no photo_annotations table")
        log.warning("TEMPORARY legacy annotation writes into %s", db_path)

    def upsert(self, path: str, source: str, payload: dict) -> None:
        with self.lock, self.conn:
            self.mod.upsert_annotation(self.conn, path, source, payload)

    def delete(self, path: str, source: str) -> bool:
        with self.lock, self.conn:
            return bool(self.mod.delete_annotation(self.conn, path, source))

    def close(self) -> None:
        self.conn.close()


def adopt_plugin_rows(ledger, library: str, db_path: str, path_prefix: str | None = None,
                      facet_version: str | None = None, dry_run: bool = False) -> dict:
    """TEMPORARY, one-time: take over what the removed in-process plugin wrote (``photo_manual_tags`` rows with
    ``created_by='astro-id'`` and ``photo_annotations`` rows with source ``astro-id``) into the ledger, so the
    sidecar can replace or remove them later. Opens the database READ-ONLY; writes only the ledger."""
    import json

    from .sync import ANNOTATION_SOURCE, payload_hash

    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        tags: dict[str, list[str]] = {}
        for path, tag in conn.execute("SELECT photo_path, tag FROM photo_manual_tags WHERE created_by = 'astro-id'"):
            tags.setdefault(path, []).append(tag)
        anns: dict[str, str] = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'photo_annotations'").fetchone():
            for path, payload in conn.execute("SELECT photo_path, payload FROM photo_annotations WHERE source = ?",
                                              (ANNOTATION_SOURCE,)):
                try:
                    anns[path] = payload_hash(json.loads(payload))
                except ValueError:
                    anns[path] = "unreadable"
    finally:
        conn.close()
    pre = path_prefix.replace("\\", "/").rstrip("/").lower() + "/" if path_prefix else None
    paths = sorted(p for p in set(tags) | set(anns) if pre is None or p.replace("\\", "/").lower().startswith(pre))
    adopted = 0
    for p in paths:
        prev = ledger.get(library, p)
        if prev is not None:
            continue                       # the sidecar already owns this photo: never overwrite its record
        if not dry_run:
            ledger.record(library, p, tags=tags.get(p, []), payload_hash=anns.get(p),
                          annotation_via="legacy-db" if p in anns else None, status="adopted",
                          facet_version=facet_version)
        adopted += 1
    return {"photos": len(paths), "adopted": adopted, "tags": sum(len(tags.get(p, [])) for p in paths),
            "annotations": sum(1 for p in paths if p in anns), "dry_run": dry_run}
