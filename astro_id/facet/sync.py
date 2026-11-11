"""Solve astro photos a Facet library holds and write the results back through Facet's HTTP API.

Shared by ``astro-id facet sync`` (batch / polling) and ``astro-id facet serve`` (the web UI):

1. candidates: ``GET /api/photos`` (category, path prefix, tag, album; every frame), minus photos the ledger
   already holds (unless ``force``), plus paths from an optional notifier spool;
2. hints: each photo's own GPS and, when its capture date was corrected in Facet, that date (as the true time);
3. solve: :func:`astro_id.session.run_session` (in process, JSON-lines events to ``emit``);
4. write: tags through ``POST /api/photos/batch_manual_tags`` / ``DELETE /api/photo/manual_tags`` and the
   annotation payload (``PUT /api/photo/annotations``, else TEMPORARY ``--legacy-db``, else skipped);
   the ledger records exactly the tags astro-id added, so a re-run removes only those.

Nothing here writes Facet's reject/cull flags or ``stats_cache`` (the tag route refreshes its own caches).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .client import FacetClient, FacetError
from .ledger import Ledger, library_key

log = logging.getLogger("astro_id.facet")

ANNOTATION_SOURCE = "astro-id"
TAG_SOLVED = "plate-solved"
TAG_FAILED = "plate-solve failed"
TAG_REVIEW = "astro-review"
MAX_TAGS_PER_PHOTO = 50      # Facet's per-photo manual tag cap (db/manual_tags.py), shared with user tags
MAX_TAG_LENGTH = 64
_UNSET = object()


# ---------------------------------------------------------------- tags ---

def normalize_tag(tag) -> str | None:
    """Facet's manual-tag canonical form (NFC, trimmed, single spaces, lowercase, no comma, <= 64 chars);
    None when Facet would refuse it. Mirrors ``db.manual_tags.normalize_manual_tag``."""
    if not isinstance(tag, str):
        return None
    text = unicodedata.normalize("NFC", tag)
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
        return None
    text = re.sub(r"\s+", " ", text.strip()).lower()
    if not text or "," in text or len(text) > MAX_TAG_LENGTH:
        return None
    return text


def _level(o: dict) -> int:
    lv = o.get("level")
    return int(lv) if isinstance(lv, (int, float)) else 9


def desired_tags(result: dict, min_confidence: int, extra: list[str] | tuple = ()) -> list[str]:
    """Tags for one frame in priority order (the cap drops from the end): status tag, ``extra``, then objects by
    level (constellation, Sun/Moon/planet, catalog object, asterism, star), confidence descending. Only objects the
    engine marked ``taggable`` and at least ``min_confidence``."""
    tags = [TAG_SOLVED if result.get("solved") else TAG_FAILED]
    for t in extra:
        n = normalize_tag(t)
        if n and n not in tags:
            tags.append(n)
    if not result.get("solved"):
        return tags
    objs = sorted((o for o in result.get("objects") or []
                   if isinstance(o.get("confidence"), (int, float)) and o["confidence"] >= min_confidence
                   and o.get("taggable", True) is not False),
                  key=lambda o: (_level(o), -o["confidence"], str(o.get("tag"))))
    for o in objs:
        n = normalize_tag(o.get("tag"))
        if n is None:
            log.warning("skipping a tag Facet would refuse: %r", o.get("tag"))
        elif n not in tags:
            tags.append(n)
    return tags


def ai_tags(photo: dict) -> set[str]:
    raw = photo.get("tags")
    items = raw.split(",") if isinstance(raw, str) else (photo.get("tags_list") or [])
    return {t.strip().lower() for t in items if t and t.strip()}


def manual_tags(photo: dict) -> set[str]:
    """Every manual tag row of the photo (content and album tags both count against the cap)."""
    return set(photo.get("manual_tags") or []) | set(photo.get("album_tags") or [])


def plan_photo(desired: list[str], manual: set[str], ai: set[str], mine: set[str],
               cap: int = MAX_TAGS_PER_PHOTO) -> dict:
    """What to change on one photo. ``mine`` = tags the ledger says astro-id added earlier.

    A tag someone else put there (user or XMP: in ``manual`` but not ``mine``) is never claimed or removed; a tag
    the AI tagger already has is skipped (Facet would skip it too); the cap counts foreign rows first."""
    mine = mine & manual                      # a tag the user deleted in Facet is no longer ours to remove
    foreign = manual - mine
    room = cap - len(foreign)
    keep, skipped, dropped = [], [], []
    for t in desired:
        if t in foreign or t in ai:
            skipped.append(t)
        elif len(keep) >= room:
            dropped.append(t)
        else:
            keep.append(t)
    add = [t for t in keep if t not in manual]
    remove = sorted(mine - set(keep))
    return {"keep": keep, "add": add, "remove": remove, "skipped": skipped, "dropped": dropped}


# ---------------------------------------------------------- annotations ---

def annotation_payload(result: dict):
    """None = the frame did not solve (remove our row); a dict = store it; ``_UNSET`` = leave it (no payload)."""
    if not result.get("solved"):
        return None
    a = result.get("annotations")
    if not (isinstance(a, dict) and a.get("version") == 1):
        return _UNSET
    q = result.get("quality")
    return {**a, "quality": q} if isinstance(q, dict) else dict(a)


def payload_hash(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ----------------------------------------------------------------- hints ---

def _valid_gps(lat, lon) -> bool:
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90
            and -180 <= lon <= 180 and not (lat == 0 and lon == 0))


def gps_source(photo: dict) -> str:
    """Where the photo's position comes from: 'facet-manual', 'facet-exif' or 'none' (Facet holds none)."""
    if not _valid_gps(photo.get("gps_latitude"), photo.get("gps_longitude")):
        return "none"
    return "facet-manual" if photo.get("gps_manually_set") else "facet-exif"


def parse_facet_date(value) -> datetime | None:
    """Facet's ``date_taken`` ('YYYY:MM:DD HH:MM:SS' or ISO, naive camera-local wall time)."""
    if not isinstance(value, str):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip()[:19], fmt)
        except ValueError:
            continue
    return None


def facet_hints(photo: dict) -> dict:
    """Session hints from what Facet holds: its GPS (manual or EXIF) as the site, and the capture date as the true
    time only when it was corrected in Facet (``date_manually_set``); an uncorrected date is the camera clock."""
    h: dict = {"site": None, "time": None}
    if _valid_gps(photo.get("gps_latitude"), photo.get("gps_longitude")):
        h["site"] = f"{photo['gps_latitude']:.6f},{photo['gps_longitude']:.6f}"
    if photo.get("date_manually_set"):
        dt = parse_facet_date(photo.get("date_taken"))
        if dt is not None:
            h["time"] = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return h


# ---------------------------------------------------------------- paths ---

def parse_path_map(items: list[str] | None) -> list[tuple[str, str]]:
    """``FACET_PREFIX=LOCAL_PREFIX`` pairs (Facet in Docker sees /photos, the sidecar sees a share)."""
    out = []
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--path-map expects FACET_PREFIX=LOCAL_PREFIX, got {it!r}")
        a, b = it.split("=", 1)
        out.append((a, b))
    return out


def _swap(path: str, pairs, forward: bool) -> str:
    norm = path.replace("\\", "/")
    for a, b in pairs:
        src, dst = (a, b) if forward else (b, a)
        s = src.replace("\\", "/").rstrip("/")
        if norm.lower() == s.lower() or norm.lower().startswith(s.lower() + "/"):
            return dst.rstrip("/\\") + norm[len(s):]
    return path


def to_local(path: str, pairs) -> str:
    return _swap(path, pairs, True) if pairs else path


# ------------------------------------------------------------ the writer ---

class LegacyUnavailable(RuntimeError):
    pass


@dataclass
class SyncContext:
    client: FacetClient
    ledger: Ledger
    dry_run: bool = False
    min_confidence: int = 65
    legacy_db: str | None = None
    facet_dir: str | None = None
    annotations: bool = True
    library: str = ""
    version: str | None = None
    mode: str | None = None              # 'api' | 'legacy-db' | None (annotations skipped)
    warnings: list = field(default_factory=list)
    addon: bool = False                  # started by Facet as an add-on: no --legacy-db, sidebar wording
    tags_via: str = "manual+ledger"      # 'manual+ledger' until Facet PR-3 plugin tags are wired here
    _legacy: object = None

    def prepare(self) -> "SyncContext":
        self.library = library_key(self.client.url)
        if getattr(self.client, "mode", None) == "mcp":
            self.tags_via = "mcp add_tags (created_by = add-on)+ledger"
        self.version = self.client.server_version()
        feats = self.client.features()
        if feats.get("plugin_tags"):
            # Detected first, as the spec asks; its request body is not pinned in PLUGIN_BOUNDARY v0.2, so the
            # writer stays on batch_manual_tags + ledger until it is (then: source='plugin', created_by = add-on id).
            self.warnings.append("this Facet has plugin tags (PR-3); astro-id still writes manual tags + its "
                                 "ledger until the plugin_tags body is pinned in the spec")
        if not self.annotations:
            self.mode = None
            return self
        if feats.get("annotations_put"):
            self.mode = "api"
        elif self.legacy_db and self.addon:
            raise ValueError("--legacy-db is not available in add-on mode")
        elif self.addon:
            self.mode = None
            self.warnings.append("Annotations skipped: this Facet has no PUT /api/photo/annotations yet (Facet "
                                 "PR-1). Tags and GPS are still written; the overlay labels appear once PR-1 is in.")
        elif self.legacy_db:
            from .legacy import LegacyAnnotationWriter

            self._legacy = LegacyAnnotationWriter(self.legacy_db, self.facet_dir)
            self.mode = "legacy-db"
            self.warnings.append("TEMPORARY: annotations are written straight into the Facet database "
                                 f"({self.legacy_db}) because this Facet has no PUT /api/photo/annotations yet")
        else:
            self.mode = None
            self.warnings.append("annotations skipped: this Facet has no PUT /api/photo/annotations "
                                 "(use --legacy-db PATH on a local stack until it does)")
        for w in self.warnings:
            log.warning("%s", w)
        return self

    def close(self):
        if self._legacy is not None:
            self._legacy.close()


def write_annotation(ctx: SyncContext, path: str, result: dict, prev: dict | None) -> tuple[str, str | None, str | None]:
    """(what happened, payload hash to record, how it was stored)."""
    payload = annotation_payload(result)
    prev_hash = prev.get("payload_hash") if prev else None
    prev_via = prev.get("annotation_via") if prev else None
    if payload is _UNSET:
        return "unchanged (engine sent no payload)", prev_hash, prev_via
    if ctx.mode is None:
        return "skipped (no annotation route)", prev_hash, prev_via
    if payload is None:
        if not prev_hash:
            return "none", None, None
        if ctx.dry_run:
            return "would delete", prev_hash, prev_via
        if ctx.mode == "api":
            ctx.client.delete_annotation(path, ANNOTATION_SOURCE)
        else:
            ctx._legacy.delete(path, ANNOTATION_SOURCE)
        return "deleted", None, None
    h = payload_hash(payload)
    if h == prev_hash and prev_via == ctx.mode:
        return "unchanged", h, prev_via
    if ctx.dry_run:
        return "would store", prev_hash, prev_via
    if ctx.mode == "api":
        ctx.client.put_annotation(path, ANNOTATION_SOURCE, payload)
    else:
        ctx._legacy.upsert(path, ANNOTATION_SOURCE, payload)
    return "stored", h, ctx.mode


def apply_results(ctx: SyncContext, results: dict[str, dict], extra_tags: dict[str, list[str]] | None = None,
                  emit: Callable[[dict], None] | None = None, tags: bool = True) -> list[dict]:
    """Write tags + annotations for ``{facet path: frame result}``. Returns one report per photo.

    Phase 1 plans every photo against a fresh ``GET /api/photo``; phase 2 removes stale astro-id tags and adds
    new ones (one batch call per tag); phase 3 re-reads each photo and records in the ledger the planned tags
    that are actually present (a cap race or a refusal never ends up in the ledger)."""
    emit = emit or (lambda ev: None)
    extra_tags = extra_tags or {}
    plans: dict[str, dict] = {}
    reports: list[dict] = []
    for path, fr in results.items():
        prev = ctx.ledger.get(ctx.library, path)
        photo = ctx.client.photo(path)
        if photo is None:
            reports.append({"path": path, "error": "not in Facet (or not visible)"})
            continue
        want = desired_tags(fr, ctx.min_confidence, extra_tags.get(path, ()))
        mine = set(prev["tags"]) if prev else set()
        plan = plan_photo(want, manual_tags(photo), ai_tags(photo), mine) if tags else \
            {"keep": sorted(mine & manual_tags(photo)), "add": [], "remove": [], "skipped": [], "dropped": []}
        plans[path] = {"plan": plan, "prev": prev, "result": fr}
    if not ctx.dry_run:
        for path, p in plans.items():
            for t in p["plan"]["remove"]:
                ctx.client.delete_tag(path, t)
        by_tag: dict[str, list[str]] = {}
        for path, p in plans.items():
            for t in p["plan"]["add"]:
                by_tag.setdefault(t, []).append(path)
        for t, paths in sorted(by_tag.items()):
            ctx.client.batch_add_tag(t, paths)
    for path, p in plans.items():
        plan, prev, fr = p["plan"], p["prev"], p["result"]
        rep = {"path": path, "status": "solved" if fr.get("solved") else "failed", "add": plan["add"],
               "remove": plan["remove"], "kept": plan["keep"], "skipped": plan["skipped"],
               "dropped": plan["dropped"], "dry_run": ctx.dry_run}
        try:
            rep["annotation"], h, via = write_annotation(ctx, path, fr, prev)
        except Exception as exc:   # never lose the ledger record of tags already written above
            rep["annotation"], h, via = f"error: {exc}", (prev or {}).get("payload_hash"), (prev or {}).get("annotation_via")
            log.error("%s: annotation not stored: %s", path, exc)
        if not ctx.dry_run:
            after = ctx.client.photo(path) or {}
            present = manual_tags(after)
            owned = [t for t in plan["keep"] if t in present]
            missing = [t for t in plan["keep"] if t not in present]
            if missing:
                rep["not_written"] = missing
                log.warning("%s: Facet did not store %s", path, missing)
            ctx.ledger.record(ctx.library, path, tags=owned, payload_hash=h, annotation_via=via,
                              status=rep["status"], facet_version=ctx.version)
        reports.append(rep)
        emit({"event": "written", **rep})
    return reports


def remove_all(ctx: SyncContext, paths: list[str] | None = None) -> list[dict]:
    """Undo: remove every ledger tag (and our annotation) from ``paths`` (default: every ledger photo)."""
    reports = []
    for row in ctx.ledger.rows(ctx.library):
        if paths is not None and row["path"] not in paths:
            continue
        photo = ctx.client.photo(row["path"]) or {}
        present = manual_tags(photo)
        gone = [t for t in row["tags"] if t in present]
        ann = "none"
        if not ctx.dry_run:
            for t in gone:
                ctx.client.delete_tag(row["path"], t)
            if row.get("payload_hash") and row.get("annotation_via"):
                if ctx.mode == "api":
                    ctx.client.delete_annotation(row["path"], ANNOTATION_SOURCE)
                    ann = "deleted"
                elif ctx.mode == "legacy-db":
                    ctx._legacy.delete(row["path"], ANNOTATION_SOURCE)
                    ann = "deleted"
                else:
                    ann = "kept (no annotation route)"
            if ann != "kept (no annotation route)":
                ctx.ledger.forget(ctx.library, row["path"])
        reports.append({"path": row["path"], "remove": gone, "annotation": ann, "dry_run": ctx.dry_run})
    return reports


# ------------------------------------------------------------- selection ---

@dataclass
class Selection:
    path_prefix: str | None = None
    category: str | None = "astro"
    tag: str | None = None
    album_id: str | None = None
    since: str | None = None             # 'YYYY-MM-DD' (Facet capture date) or 'last'
    force: bool = False
    limit: int | None = None
    spool: str | None = None


WATERMARK = "since_watermark"


def read_spool(path: str | None) -> list[str]:
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return list(dict.fromkeys(line.strip() for line in fh if line.strip()))


def select_candidates(client: FacetClient, ledger: Ledger, library: str, sel: Selection) -> tuple[list[dict], dict]:
    """(photos to solve, info). Pending = not in the ledger yet (``force``: all), plus spooled paths."""
    since = sel.since
    if since == "last":
        since = ledger.get_meta(library, WATERMARK)
    listed = client.list_photos(path_prefix=sel.path_prefix, category=sel.category or None, tag=sel.tag,
                                album_id=sel.album_id, date_from=since)
    known = ledger.known_paths(library)
    picked = [p for p in listed if sel.force or p["path"] not in known]
    have = {p["path"] for p in picked}
    for sp in read_spool(sel.spool):
        if sp in have or (sp in known and not sel.force):
            continue
        ph = client.photo(sp)
        if ph is not None:
            picked.append(ph)
            have.add(sp)
    if sel.limit:
        picked = picked[:sel.limit]
    dates = [p.get("date_taken") for p in listed if parse_facet_date(p.get("date_taken"))]
    newest = max((parse_facet_date(d) for d in dates), default=None)
    info = {"listed": len(listed), "already_synced": sum(1 for p in listed if p["path"] in known),
            "since": since, "watermark": newest.strftime("%Y-%m-%d") if newest else None}
    return picked, info


# ------------------------------------------------------------------ run ---

def solve_and_write(ctx: SyncContext, photos: list[dict], session_opts, emit=None, path_map=None,
                    extra_tags: dict | None = None, run_session_fn=None) -> dict:
    """Solve ``photos`` (Facet photo dicts) with ``session_opts`` (a SessionOptions; its hints are filled from
    Facet here) and write the results. Returns the session's done event plus ``reports``."""
    from ..session import run_session

    run_session_fn = run_session_fn or run_session
    emit = emit or (lambda ev: None)
    local = {to_local(p["path"], path_map): p for p in photos}
    hints = dict(session_opts.hints or {})
    for lp, ph in local.items():
        h = facet_hints(ph)
        cur = hints.get(lp) or {}
        hints[lp] = {"site": cur.get("site") or h["site"], "time": cur.get("time") or h["time"]}
    session_opts.hints = hints
    done = run_session_fn(list(local), session_opts, emit)
    results = {local[lp]["path"]: fr for lp, fr in (done.get("results") or {}).items() if lp in local}
    reports = apply_results(ctx, results, extra_tags=extra_tags, emit=emit)
    return {**{k: v for k, v in done.items() if k != "results"}, "results": results, "reports": reports}
