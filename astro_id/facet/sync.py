"""Solve astro photos a Facet library holds and write the results back through Facet's HTTP API.

Shared by ``astro-id facet sync`` (batch / polling) and ``astro-id facet serve`` (the web UI):

1. candidates: ``GET /api/photos`` (category, path prefix, tag, album; every frame), minus photos the ledger
   already holds (unless ``force``), plus paths from an optional notifier spool;
2. hints: each photo's own GPS and, when its capture date was corrected in Facet, that date (as the true time);
3. solve: :func:`astro_id.session.run_session` (in process, JSON-lines events to ``emit``);
4. write: tags through ``POST /api/photos/batch_manual_tags`` / ``DELETE /api/photo/manual_tags`` and the
   annotation payload (``PUT /api/photo/annotations``, else TEMPORARY ``--legacy-db``, else skipped);
   the ledger records exactly the tags astro-id added, so a re-run removes only those.

Model D (a Facet whose ``status`` reports add-on ``features``; see mcp_client): per photo at most 3 summary tags
(``plate-solved`` / ``plate-solve failed``, ``astro-review``) written with ``plugin: true``, and ONE annotation
payload whose items carry ``index: true`` for the names at or above the save cut: Facet turns those into searchable
detections. No per-object tags. The first model D save of a photo removes the per-object tags astro-id wrote
earlier (the ledger lists them; ``remove_tags`` only ever deletes this add-on's own rows), so re-saving converges.
An older Facet keeps the per-object tags exactly as before.

When the ledger is behind Facet (B-P11: a record with ``tags=[]``, no record, a ledger from another version or
machine) a save first takes back the rows astro-id wrote: on any Facet whose ``get_photo`` carries
``manual_tag_sources`` (``{tag: add-on id}``) those rows are adopted; on model D (which lists only plugin rows)
the plain rows with a name astro-id could have written are offered once to ``remove_tags``, which removes only
the add-on's own. On an older Facet that says neither, nothing unattributed is removed (its ``remove_tags`` deletes
any row with that text) and the report lists those rows as ``unknown_owner``.

Nothing here writes Facet's reject/cull flags or ``stats_cache`` (the tag route refreshes its own caches).
"""
from __future__ import annotations

import contextlib
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
MAX_PLUGIN_TAGS = 3          # model D: summary tags per photo per add-on (db/manual_tags.MAX_PLUGIN_TAGS_PER_PHOTO)
TAG_KIND_PLUGIN = "plugin"
TAG_KIND_LEGACY = "legacy-foreign"   # per-object tags written through the user path: not ours to remove
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


def summary_tags(result: dict, extra: list[str] | tuple = ()) -> list[str]:
    """Model D: the status tag, then ``extra`` (e.g. ``astro-review``), at most :data:`MAX_PLUGIN_TAGS`."""
    tags = [TAG_SOLVED if result.get("solved") else TAG_FAILED]
    for t in extra:
        n = normalize_tag(t)
        if n and n not in tags:
            tags.append(n)
    return tags[:MAX_PLUGIN_TAGS]


def searchable(o: dict, min_confidence: int) -> bool:
    """A name that is saved: at or above the cut and marked ``taggable`` by the engine's policy (level on, not
    covered by its constellation / cluster / asterism). The same rule made tags before model D."""
    c = o.get("confidence")
    return isinstance(c, (int, float)) and c >= min_confidence and o.get("taggable", True) is not False


def index_payload(payload: dict, result: dict, min_confidence: int) -> dict:
    """Model D: a copy of ``payload`` with ``index: true`` on the items whose names :func:`searchable` keeps
    (matched to the frame's objects by name). Facet copies those into its detections index; the others stay sky
    labels only. Only true is written (no ``index_groups``, so absent = not indexed): a few bytes per saved name,
    never per star, so the payload stays under Facet's 512 KB with astro-id's own 500 KB cap."""
    keep: dict[str, bool] = {}
    for o in result.get("objects") or []:
        name = o.get("name")
        if isinstance(name, str):
            keep[name] = keep.get(name, False) or searchable(o, min_confidence)
    items = []
    for it in payload.get("items") or []:
        if isinstance(it, dict):
            it = {k: v for k, v in it.items() if k != "index"}
            if keep.get(it.get("label")):
                it["index"] = True
        items.append(it)
    return {**payload, "items": items}


def ai_tags(photo: dict) -> set[str]:
    raw = photo.get("tags")
    items = raw.split(",") if isinstance(raw, str) else (photo.get("tags_list") or [])
    return {t.strip().lower() for t in items if t and t.strip()}


def manual_tags(photo: dict) -> set[str]:
    """Every manual tag row of the photo (content and album tags both count against the cap; a model D Facet
    also lists add-on summary tags in ``plugin_tags``)."""
    plugin = {t.get("tag") for t in photo.get("plugin_tags") or [] if isinstance(t, dict) and t.get("tag")}
    return set(photo.get("manual_tags") or []) | set(photo.get("album_tags") or []) | plugin


def plan_photo(desired: list[str], manual: set[str], ai: set[str], mine: set[str],
               cap: int = MAX_TAGS_PER_PHOTO, plugin: bool = False, migrate: bool = False) -> dict:
    """What to change on one photo. ``mine`` = tags the ledger says astro-id added earlier.

    A tag someone else put there (user or XMP: in ``manual`` but not ``mine``) is never claimed or removed; a tag
    the AI tagger already has is skipped (Facet would skip it too); the cap counts foreign rows first.

    ``plugin`` (model D summary tags): the cap is the add-on's own, so foreign rows do not use it. ``migrate``: the
    ledger's tags were written as plain manual rows (an older Facet), so every one of them is removed and the kept
    ones are added again as summary tags."""
    mine = mine & manual                      # a tag the user deleted in Facet is no longer ours to remove
    foreign = manual - mine
    room = cap if plugin else cap - len(foreign)
    keep, skipped, dropped = [], [], []
    for t in desired:
        if t in foreign or t in ai:
            skipped.append(t)
        elif len(keep) >= room:
            dropped.append(t)
        else:
            keep.append(t)
    if migrate:
        add = list(keep)
        remove = sorted(mine)
    else:
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
    tags_via: str = "manual+ledger"      # 'manual+ledger'; model D: plugin summary tags + ledger
    model_d: bool = False                # summary plugin tags + indexed detections (see the module docstring)
    annotations_batch: bool = False      # set_annotations_batch available (model D)
    uncertain: bool = False              # the client could not tell old from model D yet: asked again on use
    addon_id: str | None = None          # the id Facet records as created_by for this add-on (default astro-id)
    _legacy: object = None

    def prepare(self) -> "SyncContext":
        self.library = library_key(self.client.url)
        if getattr(self.client, "mode", None) == "mcp":
            self.tags_via = "mcp add_tags (created_by = add-on)+ledger"
        self.version = self.client.server_version()
        feats = self._detect()
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

    def _detect(self) -> dict:
        feats = self.client.features()
        self.uncertain = bool(feats.get("uncertain"))
        # Model D tag mode (summary tags, no per-object tags) only when the names can be stored as searchable
        # detections: otherwise removing the old per-object tags would leave the names unfindable.
        can_index = bool(self.annotations and feats.get("annotations_put"))
        was = self.model_d
        self.model_d = bool(feats.get("model_d") and feats.get("plugin_tags") and can_index)
        self.annotations_batch = bool(self.model_d and feats.get("annotations_batch"))
        if self.model_d:
            self.tags_via = "plugin summary tags+ledger; names as searchable detections"
        elif feats.get("model_d") and feats.get("plugin_tags") and not was:
            w = ("Names stay per-object tags: sky labels are off or this add-on cannot store them, "
                 "so Facet cannot make the names searchable")
            if w not in self.warnings:
                self.warnings.append(w)
        return feats

    def refresh(self) -> "SyncContext":
        """Ask the Facet again when the first look was a guess (its ``status`` call had failed)."""
        if self.uncertain:
            was = self.model_d
            self._detect()
            if self.model_d != was:
                log.info("Facet features now known: model D %s", self.model_d)
        return self

    def close(self):
        if self._legacy is not None:
            self._legacy.close()


def write_annotation(ctx: SyncContext, path: str, result: dict, prev: dict | None,
                     batch: dict | None = None, info: dict | None = None) -> tuple[str, str | None, str | None]:
    """(what happened, payload hash to record, how it was stored). Model D marks the searchable items first.
    ``batch`` (a dict): a payload to store goes there (``{path: payload}``) for one ``set_annotations_batch``
    instead of being written now, and the answer is "pending". ``info`` receives ``searchable`` (Facet's
    ``indexed``) when a single write answers it."""
    payload = annotation_payload(result)
    if ctx.model_d and isinstance(payload, dict):
        payload = index_payload(payload, result, ctx.min_confidence)
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
    if ctx.mode == "api" and batch is not None:
        batch[path] = payload
        return "pending", h, ctx.mode
    if ctx.mode == "api":
        r = ctx.client.put_annotation(path, ANNOTATION_SOURCE, payload) or {}
        if info is not None and isinstance(r.get("indexed"), int):
            info["searchable"] = r["indexed"]
    else:
        ctx._legacy.upsert(path, ANNOTATION_SOURCE, payload)
    return "stored", h, ctx.mode


def _job(client):
    """The client's one-session-per-job block (MCP), or nothing for a client without one."""
    job = getattr(client, "job", None)
    return job() if callable(job) else contextlib.nullcontext()


def apply_results(ctx: SyncContext, results: dict[str, dict], extra_tags: dict[str, list[str]] | None = None,
                  emit: Callable[[dict], None] | None = None, tags: bool = True) -> list[dict]:
    """Write tags + annotations for ``{facet path: frame result}``. Returns one report per photo.

    One MCP session for the whole call. Phase 1 plans every photo against a fresh ``get_photo``; phase 2 removes
    stale astro-id tags and adds new ones (one call per tag, all its photos); phase 3 stores the annotation payloads
    (model D: one ``set_annotations_batch`` per 200 photos); phase 4 re-reads each photo and records in the ledger
    the planned tags that are actually present (a cap race or a refusal never ends up in the ledger)."""
    ctx.refresh()
    with _job(ctx.client):
        return _apply_results(ctx, results, extra_tags or {}, emit or (lambda ev: None), tags)


def _adopt(photo: dict) -> set[str]:
    """Model D: the summary tags Facet itself says this add-on wrote (``plugin_tags`` with ``source`` astro-id), so a
    row whose ledger record was lost (a failure in the middle of an earlier save) is ours again."""
    return {t["tag"] for t in photo.get("plugin_tags") or []
            if isinstance(t, dict) and t.get("tag") and t.get("source") == ANNOTATION_SOURCE}


def _owner_ids(ctx) -> set[str]:
    return {ANNOTATION_SOURCE} | ({ctx.addon_id} if ctx.addon_id else set())


def _sources(photo: dict, owners: set[str]) -> set[str] | None:
    """The manual tags Facet says this add-on wrote (``manual_tag_sources`` ``{tag: add-on id}`` on ``get_photo``,
    Facet local/photo-page-layout and later), or None when this Facet does not say (the key is absent)."""
    src = photo.get("manual_tag_sources")
    if not isinstance(src, dict):
        return None
    return {t for t, who in src.items() if isinstance(t, str) and who in owners}


def candidate_tags(result: dict, extra: list[str] | tuple = ()) -> set[str]:
    """Every tag astro-id could have written for this frame at ANY cut: the status/review tags, ``extra`` and the
    tag of every object the engine named (taggable or not, any confidence: an earlier version or policy may have
    tagged it). Used only to decide which rows to ask about, never as proof of ownership."""
    out = {TAG_SOLVED, TAG_FAILED, TAG_REVIEW}
    for t in [*extra, *(o.get("tag") for o in result.get("objects") or [])]:
        n = normalize_tag(t)
        if n:
            out.add(n)
    return out


def _ledger_behind(prev: dict | None) -> bool:
    """Model D: the ledger has never recorded a model D save of this photo (no record, or one from an older
    contract / an earlier version), so rows astro-id wrote may be missing from it."""
    return prev is None or prev.get("tag_kind") != TAG_KIND_PLUGIN


def _probe_own_rows(ctx, looked: dict[str, dict]) -> dict[str, list[str]]:
    """Model D, ledger behind (B-P11): ``get_photo`` lists only plugin summary rows, so the old plain rows astro-id
    wrote (per-object tags, a plain ``plate-solved``) look like anyone's. Model D's ``remove_tags`` removes only the
    add-on's own rows and leaves the others (``skipped_foreign``), so the plain rows with a name astro-id could
    have written are offered to it once: what Facet removes was astro-id's; what stays is the user's (or another
    writer's) and is never touched. Re-reads the photos it changed. ``{path: [removed tags]}``."""
    by_tag: dict[str, list[str]] = {}
    asked: dict[str, set[str]] = {}
    for path, it in looked.items():
        photo, prev = it["photo"], it["prev"]
        if not _ledger_behind(prev):
            continue
        plugin = {t.get("tag") for t in photo.get("plugin_tags") or [] if isinstance(t, dict)}
        ledger_tags = set(prev["tags"]) if prev else set()
        # ledger tags are already removed by the migration below; the AI's tags are not manual rows
        ask = (set(photo.get("manual_tags") or []) - plugin - ledger_tags) & it["candidates"]
        if ask:
            asked[path] = ask
            for t in ask:
                by_tag.setdefault(t, []).append(path)
    if not by_tag:
        return {}
    for t, paths in sorted(by_tag.items()):
        done = ctx.client.batch_remove_tag(t, paths) or {}
        if done.get("skipped_foreign"):
            log.info("%r: %d row(s) are not astro-id's (the user's): left in place", t, done["skipped_foreign"])
    removed: dict[str, list[str]] = {}
    for path, ask in asked.items():
        after = ctx.client.photo(path)
        if after is None:
            continue
        gone = sorted(ask - manual_tags(after))
        looked[path]["photo"] = after
        if gone:
            removed[path] = gone
            log.info("%s: removed astro-id's old rows the ledger did not list: %s", path, gone)
    return removed


def _apply_results(ctx, results, extra_tags, emit, tags) -> list[dict]:
    plans: dict[str, dict] = {}
    reports: list[dict] = []
    owners = _owner_ids(ctx)
    looked: dict[str, dict] = {}
    for path, fr in results.items():
        prev = ctx.ledger.get(ctx.library, path)
        photo = ctx.client.photo(path)
        if photo is None:
            reports.append({"path": path, "error": "not in Facet (or not visible)"})
            continue
        looked[path] = {"photo": photo, "prev": prev, "result": fr,
                        "candidates": candidate_tags(fr, extra_tags.get(path, ()))}
    probed: dict[str, list[str]] = {}
    if ctx.model_d and tags and not ctx.dry_run:
        probed = _probe_own_rows(ctx, looked)
    for path, it in looked.items():
        photo, prev, fr = it["photo"], it["prev"], it["result"]
        ledger_tags = set(prev["tags"]) if prev else set()
        present = manual_tags(photo)
        mine = set(ledger_tags)
        # B-P11: rows Facet itself says astro-id wrote are astro-id's even when the ledger lost them (an earlier
        # save that took them for the user's recorded tags=[], a ledger from another machine or version).
        sources = _sources(photo, owners)
        adopted = sorted((sources or set()) & present - ledger_tags)
        mine |= set(adopted)
        if ctx.model_d:
            mine |= _adopt(photo) & present
        if not tags:
            plan = {"keep": sorted(mine & present), "add": [], "remove": [], "skipped": [], "dropped": []}
        elif ctx.model_d:
            plain = (ledger_tags if (prev or {}).get("tag_kind") != TAG_KIND_PLUGIN else set()) | set(adopted)
            plain -= _adopt(photo)
            migrate = bool(plain & present)
            plan = plan_photo(summary_tags(fr, extra_tags.get(path, ())), present, ai_tags(photo), mine,
                              cap=MAX_PLUGIN_TAGS, plugin=True, migrate=migrate)
            plan["migrated"] = sorted(plain & present) if migrate else []
        else:
            plan = plan_photo(desired_tags(fr, ctx.min_confidence, extra_tags.get(path, ())), present,
                              ai_tags(photo), mine)
            if sources is None:
                # This Facet does not say who wrote a row, and its remove_tags deletes any row with that text: a
                # tag with an object's name astro-id has no record of may be the user's, so it is left alone.
                plan["unknown_owner"] = sorted((present - mine) & it["candidates"])
        if adopted:
            plan["adopted"] = adopted
        if probed.get(path):
            plan["probed"] = probed[path]
            plan["migrated"] = sorted(set(plan.get("migrated") or []) | set(probed[path]))
        plans[path] = {"plan": plan, "prev": prev, "result": fr}
    # What a photo's annotation is worth if its write never happens: the ledger's own previous state.
    ann: dict[str, tuple] = {}
    info: dict[str, dict] = {p: {} for p in plans}
    for path, p in plans.items():
        prev = p["prev"] or {}
        ann[path] = ("not written", prev.get("payload_hash"), prev.get("annotation_via"))
    try:
        _write_all(ctx, plans, ann, info)
    finally:
        # Even when a write above failed half way, record per photo what Facet now holds (one bad read does not
        # drop the others); a model D save re-adopts whatever could not be recorded (``_adopt``).
        for path, p in plans.items():
            try:
                rep = _record_photo(ctx, tags, path, p, ann[path], info[path])
            except Exception as exc:
                log.error("%s: could not record the save: %s", path, exc)
                rep = {"path": path, "error": f"could not record the save: {exc}"}
            reports.append(rep)
            if not rep.get("error"):
                emit({"event": "written", **rep})
    return reports


def _write_all(ctx, plans, ann, info) -> None:
    if ctx.dry_run:
        for path, p in plans.items():
            ann[path] = write_annotation(ctx, path, p["result"], p["prev"], None, info[path])
        return
    by_tag: dict[str, list[str]] = {}
    for path, p in plans.items():
        for t in p["plan"]["remove"]:
            by_tag.setdefault(t, []).append(path)
    for t, paths in sorted(by_tag.items()):
        done = ctx.client.batch_remove_tag(t, paths) or {}
        if done.get("skipped_foreign"):
            log.warning("%r: Facet left %d row(s) another writer owns", t, done["skipped_foreign"])
    by_tag = {}
    for path, p in plans.items():
        for t in p["plan"]["add"]:
            by_tag.setdefault(t, []).append(path)
    for t, paths in sorted(by_tag.items()):
        if ctx.model_d:
            ctx.client.batch_add_tag(t, paths, plugin=True)
        else:
            ctx.client.batch_add_tag(t, paths)
    # Annotations: single writes now; on model D the stores are collected for set_annotations_batch.
    batch: dict | None = {} if ctx.annotations_batch else None
    pending: dict[str, tuple] = {}
    for path, p in plans.items():
        try:
            what, h, via = write_annotation(ctx, path, p["result"], p["prev"], batch, info[path])
            if what == "pending":
                pending[path] = (h, via)      # becomes "stored" only when its chunk is stored
            else:
                ann[path] = (what, h, via)
        except Exception as exc:   # never lose the ledger record of tags already written above
            prev = p["prev"] or {}
            ann[path] = (f"error: {exc}", prev.get("payload_hash"), prev.get("annotation_via"))
            log.error("%s: annotation not stored: %s", path, exc)
    if batch:
        try:
            answers = ctx.client.put_annotations_batch(batch)
        except Exception as exc:
            answers = {pp: {"ok": False, "message": str(exc)} for pp in batch}
        for path in batch:
            r = answers.get(path) or {}
            prev = plans[path]["prev"] or {}
            if r.get("ok"):
                ann[path] = ("stored",) + pending[path]
                if isinstance(r.get("indexed"), int):
                    info[path]["searchable"] = r["indexed"]
                if r.get("index_skipped"):
                    info[path]["not_searchable"] = r["index_skipped"]
            else:
                ann[path] = (f"error: {r.get('error') or ''} {r.get('message') or ''}".strip(),
                             prev.get("payload_hash"), prev.get("annotation_via"))
                log.error("%s: annotation not stored: %s", path, ann[path][0])


def _record_photo(ctx, tags, path, p, annotation, info) -> dict:
    plan, prev, fr = p["plan"], p["prev"], p["result"]
    rep = {"path": path, "status": "solved" if fr.get("solved") else "failed", "add": plan["add"],
           "remove": plan["remove"], "kept": plan["keep"], "skipped": plan["skipped"],
           "dropped": plan["dropped"], "dry_run": ctx.dry_run}
    for k in ("migrated", "adopted", "unknown_owner"):
        if plan.get(k):
            rep[k] = plan[k]
    if ctx.model_d:
        rep["names"] = sorted({o["name"] for o in fr.get("objects") or []
                               if isinstance(o.get("name"), str) and searchable(o, ctx.min_confidence)})
    rep["annotation"], h, via = annotation
    rep.update(info)
    if ctx.dry_run:
        return rep
    after = ctx.client.photo(path) or {}
    present = manual_tags(after)
    # What Facet actually did (B-P11: the panel's "+n -m" are these, not the plan)
    rep["added"] = [t for t in plan["add"] if t in present]
    rep["removed"] = [t for t in plan["remove"] if t not in present and t not in plan["keep"]]
    owned = [t for t in plan["keep"] if t in present]
    missing = [t for t in plan["keep"] if t not in present]
    if missing:
        rep["not_written"] = missing
        log.warning("%s: Facet did not store %s", path, missing)
    left = [t for t in plan["remove"] if t in present and t not in plan["keep"]]
    legacy = None
    if ctx.model_d:
        # Rows the add-on may not remove (written through the user/password path, so the user owns them) stay in
        # the ledger under their own kind and are counted for the panel, never dropped silently.
        old_legacy = (prev or {}).get("legacy_tags") or []
        legacy = sorted({t for t in [*old_legacy, *left] if t in present and t not in owned})
        rep["legacy"] = legacy
        if left:
            rep["not_removed"] = left
            # "migrated" = old rows actually removed: one Facet left (the user's) is counted once, as legacy.
            # P3 2026-11-15: the panel said "232 old tags removed, 3 yours" when 229 were removed.
            if rep.get("migrated"):
                rep["migrated"] = [t for t in rep["migrated"] if t not in left]
    elif left:
        rep["not_removed"] = left        # another writer owns that row now: no longer ours to track
    kind = (prev or {}).get("tag_kind") if not tags else (TAG_KIND_PLUGIN if ctx.model_d else None)
    ctx.ledger.record(ctx.library, path, tags=owned, payload_hash=h, annotation_via=via,
                      status=rep["status"], facet_version=ctx.version, tag_kind=kind, legacy_tags=legacy)
    return rep


def remove_all(ctx: SyncContext, paths: list[str] | None = None) -> list[dict]:
    """Undo: remove every ledger tag (and our annotation) from ``paths`` (default: every ledger photo)."""
    with _job(ctx.client):
        return _remove_all(ctx, paths)


def _remove_all(ctx: SyncContext, paths: list[str] | None) -> list[dict]:
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


# ------------------------------------------------------------------ undo ---

STATUS_TAGS = frozenset({TAG_SOLVED, TAG_FAILED, TAG_REVIEW})
NOT_REMOVED_WHY = {
    "yours": "yours: written outside the add-on, so only you can remove them in Facet",
    "other_writer": "another writer owns that row now",
    "failed": "Facet did not answer: try Undo again",
    "no_route": "this Facet cannot remove sky labels",
}


def undo(ctx: SyncContext, paths: list[str] | None = None, dry_run: bool = False) -> dict:
    """Undo "Save to Facet" for ``paths`` (default: every photo the ledger holds for this library): remove the status
    tags, object tags and sky labels astro-id wrote, and nothing else.

    What is astro-id's comes from the ledger (plus, on a model D Facet, the summary tags Facet itself lists as this
    add-on's, as a save adopts them). Never removed: a tag the ledger does not list (the user's, the AI's, another
    tool's), and ``legacy_tags`` (rows written through the user path that the user owns now). A model D Facet refuses
    to remove a row another writer owns (``skipped_foreign``): counted, never forced. ``dry_run`` reads only and
    says what would go. Idempotent: what is gone is no longer in the ledger, so a second undo finds nothing.

    The ledger stays consistent on a partial failure: each photo is re-read after the writes and its row records
    exactly what of astro-id's is still there (a failed removal stays listed, so Undo again finishes it)."""
    ctx.refresh()
    with _job(ctx.client):
        return _undo(ctx, paths, dry_run)


def _undo_plan(ctx: SyncContext, row: dict) -> dict:
    """What undo removes from one ledger photo (reads Facet; writes nothing)."""
    path = row["path"]
    photo = ctx.client.photo(path)
    if photo is None:
        return {"path": path, "error": "not in Facet (or not visible)"}
    present = manual_tags(photo)
    legacy = set(row.get("legacy_tags") or []) & present
    mine = set(row["tags"]) & present
    if ctx.model_d:
        mine |= _adopt(photo) & present
    mine -= legacy
    names = 0
    labels = recorded = bool(row.get("payload_hash") and row.get("annotation_via"))
    if ctx.model_d and ctx.client.features().get("annotation_summary"):
        # the sky labels Facet holds for this add-on, and how many object tags (indexed names) they carry
        try:
            mine_ann = next((r for r in ctx.client.annotations_summary(path)
                             if r.get("source") == ANNOTATION_SOURCE), None)
            labels = mine_ann is not None
            names = int(mine_ann.get("indexed") or 0) if mine_ann else 0
        except FacetError as exc:
            if exc.status != 404:
                log.warning("%s: could not read its sky labels: %s", path, exc)
    route = ctx.mode in ("api", "legacy-db")
    return {"path": path, "row": row,
            "status_tags": sorted(t for t in mine if t in STATUS_TAGS),
            "object_tags": sorted(t for t in mine if t not in STATUS_TAGS),
            "names": names if (labels and route) else 0,
            "sky_labels": labels and route, "labels_blocked": labels and not route,
            "labels_gone": recorded and not labels,     # the ledger lists sky labels Facet no longer has
            "yours": sorted(legacy)}


def _undo(ctx: SyncContext, paths, dry_run: bool) -> dict:
    want = None if paths is None else set(paths)
    rows = [r for r in ctx.ledger.rows(ctx.library) if want is None or r["path"] in want]
    plans: dict[str, dict] = {}
    errors: list[dict] = []
    for row in rows:
        try:
            p = _undo_plan(ctx, row)
        except FacetError as exc:
            p = {"path": row["path"], "error": f"could not read the photo: {exc}"}
        if p.get("error"):
            errors.append(p)
        else:
            plans[row["path"]] = p
    failed_tags: dict[str, set] = {}
    labels_ok: dict[str, bool] = {}
    if not dry_run:
        try:
            _undo_write(ctx, plans, failed_tags, labels_ok)
        finally:
            for path, p in plans.items():
                _undo_record(ctx, p, failed_tags.get(path, set()), labels_ok)
    reports = []
    nr = {"yours": 0, "other_writer": 0, "failed": 0, "no_route": 0}
    tot = {"photos": 0, "object_tags": 0, "status_tags": 0, "sky_labels": 0}
    for path, p in plans.items():
        rep = {"path": path, "dry_run": dry_run, "not_removed": []}
        for t in p["yours"]:
            rep["not_removed"].append({"tag": t, "why": "yours"})
        failed = failed_tags.get(path, set())
        left = p.get("left", set())
        removed = {k: [t for t in p[k] if t not in failed and t not in left] for k in ("status_tags", "object_tags")}
        for t in sorted((set(p["status_tags"]) | set(p["object_tags"])) - set(removed["status_tags"])
                        - set(removed["object_tags"])):
            rep["not_removed"].append({"tag": t, "why": "failed" if t in failed else "other_writer"})
        labels = p["sky_labels"] and (dry_run or labels_ok.get(path, False))
        if p["sky_labels"] and not labels:
            rep["not_removed"].append({"sky_labels": True, "why": "failed"})
        if p["labels_blocked"]:
            rep["not_removed"].append({"sky_labels": True, "why": "no_route"})
        rep.update(status_tags=removed["status_tags"], object_tags=removed["object_tags"],
                   names=p["names"] if labels else 0, sky_labels=labels)
        rep["changed"] = bool(rep["status_tags"] or rep["object_tags"] or labels)
        for x in rep["not_removed"]:
            nr[x["why"]] += 1
        if rep["changed"]:
            tot["photos"] += 1
        tot["object_tags"] += len(rep["object_tags"]) + rep["names"]
        tot["status_tags"] += len(rep["status_tags"])
        tot["sky_labels"] += int(labels)
        reports.append(rep)
    reports += [{"path": e["path"], "error": e["error"], "dry_run": dry_run} for e in errors]
    return {"dry_run": dry_run, **tot, "not_removed": {**nr, "total": sum(nr.values())},
            "why": {k: NOT_REMOVED_WHY[k] for k, v in nr.items() if v}, "errors": len(errors),
            "recorded": len(rows), "reports": reports}


def _undo_write(ctx, plans, failed_tags, labels_ok) -> None:
    by_tag: dict[str, list[str]] = {}
    for path, p in plans.items():
        for t in p["status_tags"] + p["object_tags"]:
            by_tag.setdefault(t, []).append(path)
    for t, ps in sorted(by_tag.items()):
        try:
            done = ctx.client.batch_remove_tag(t, ps) or {}
            if done.get("skipped_foreign"):
                log.warning("undo %r: Facet left %d row(s) another writer owns", t, done["skipped_foreign"])
        except Exception as exc:       # the next tag still goes; the ledger keeps this one listed
            log.error("undo %r: not removed: %s", t, exc)
            for pp in ps:
                failed_tags.setdefault(pp, set()).add(t)
    for path, p in plans.items():
        if not p["sky_labels"]:
            continue
        try:
            if ctx.mode == "api":
                ctx.client.delete_annotation(path, ANNOTATION_SOURCE)
            else:
                ctx._legacy.delete(path, ANNOTATION_SOURCE)
            labels_ok[path] = True
        except Exception as exc:
            log.error("%s: sky labels not removed: %s", path, exc)
            labels_ok[path] = False


def _undo_record(ctx, p: dict, failed: set, labels_ok: dict) -> None:
    """Re-read one photo and make its ledger row say exactly what of astro-id's is still there."""
    row, path = p["row"], p["path"]
    tried = set(p["status_tags"]) | set(p["object_tags"])
    gone_labels = (p["sky_labels"] and labels_ok.get(path, False)) or p.get("labels_gone")
    h, via = (None, None) if gone_labels else (row.get("payload_hash"), row.get("annotation_via"))
    try:
        after = ctx.client.photo(path)
    except Exception as exc:
        log.error("%s: could not re-read after undo: %s", path, exc)
        after = None
    if after is None:
        # unknown: keep listing every tag (the next undo intersects with what Facet holds), drop removed labels
        ctx.ledger.record(ctx.library, path, tags=row["tags"], payload_hash=h, annotation_via=via,
                          status=row.get("status"), facet_version=row.get("facet_version"),
                          tag_kind=row.get("tag_kind"))
        return
    present = manual_tags(after)
    left = {t for t in tried if t in present}
    still = sorted(left & failed)                 # the call failed: still ours, Undo again removes it
    foreign = left - failed                       # Facet kept it: another writer owns that row
    p["left"] = foreign
    if not still and h is None:
        # nothing of astro-id's is left. Rows the user owns (legacy, foreign) are theirs now: reported by this undo,
        # not tracked any more (the photo is "not saved by Astro" again, as before its first save).
        ctx.ledger.forget(ctx.library, path)
        return
    legacy = sorted((set(row.get("legacy_tags") or []) & present) | (foreign if ctx.model_d else set()))
    ctx.ledger.record(ctx.library, path, tags=still, payload_hash=h, annotation_via=via, status=row.get("status"),
                      facet_version=row.get("facet_version"), tag_kind=row.get("tag_kind"), legacy_tags=legacy)


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
