"""Facet over MCP: the add-on's data channel (PLUGIN_BOUNDARY.md v0.3 §6a, R-MCP).

An add-on token (``FACET_TOKEN``, ``fct_...``) opens Facet's ``POST /mcp`` (streamable HTTP, stateless) and is refused
on ``/api`` except for the image bytes (``GET /thumbnail``). The tools it sees are the ones its permissions grant:

- ``photos.read``: ``status``, ``search_photos`` (with the ``scan_run`` filter), ``get_photo``, ``filter_options``,
  ``list_albums``;
- ``tags.write``: ``add_tags``, ``remove_tags`` (Facet records the add-on as ``created_by``);
- ``gps.write``: ``set_gps``, for a whole folder or album, never one photo (R-LOC);
- ``annotations.write``: ``get_annotations``, ``set_annotations``, ``clear_annotations`` (Facet PR-1; the
  source is always the add-on id), plus ``set_annotations_batch`` on a "model D" Facet.

Model D (Facet fix/review-all, docs/CONFIGURATION.md "Add-ons"): ``status`` answers an add-on token with
``facet_api``, ``annotation_payload``, ``annotation_shapes`` and ``features``. Then ``add_tags`` takes
``plugin: true`` (summary tags, at most 3 per photo per add-on), ``remove_tags`` deletes only this add-on's rows
(``skipped_foreign``), annotation items marked ``index: true`` become searchable detections, and
``get_annotations`` takes ``summary: true``. An older Facet reports none of this; :meth:`McpFacetClient.features`
says which one is on the other end (``model_d``).

:class:`McpFacetClient` has the same surface as the ``/api`` :class:`~astro_id.facet.client.FacetClient`, so the
sync and session code does not care which one it holds. Outside a job each call opens one short MCP connection
(``asyncio.run``). Inside ``with client.job():`` the calling thread keeps ONE session open (on its own event-loop
thread) for every call it makes, so saving 200 photos is one ``initialize``, not hundreds. A ``rate_limited``
answer is retried after Facet's ``retry_after_s`` (add-on tokens: 600 reads / 300 writes per minute).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable
from urllib.parse import quote

import requests

from .client import FacetError

log = logging.getLogger("astro_id.facet")
# One line per MCP request at INFO would drown the add-on's own log.
for _noisy in ("httpx2", "httpx", "mcp", "mcp.client", "mcp.client.streamable_http"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

MCP_PATH = "/mcp"
PAGE_SIZE = 100          # search_photos per_page maximum
TAG_BATCH = 1000         # add_tags / remove_tags paths per call
ANN_BATCH = 200          # set_annotations_batch photos per call
ANN_BATCH_BYTES = 9 * 1024 * 1024   # under Facet's 10 MB per set_annotations_batch call
RATE_RETRIES = 3
MAX_RETRY_WAIT_S = 60.0
# Every frame of a set (the viewer hides bursts, duplicates ... by default; a solver wants all of them).
ALL_FRAMES = {"hide_blinks": False, "hide_bursts": False, "hide_duplicates": False, "hide_brackets": False,
              "hide_panoramas": False, "hide_rejected": False}
# Columns the session needs beyond the compact search row.
ROW_FIELDS = ["gps_latitude", "gps_longitude", "gps_manually_set", "date_manually_set"]
_STATUS = {"forbidden_level": 403, "feature_disabled": 403, "not_found": 404, "invalid_input": 422,
           "too_large": 413, "conflict": 409, "rate_limited": 429}


class _HeldSession:
    """One MCP session kept open on its own event-loop thread (``McpFacetClient.job``).

    The SDK client is entered and left inside one task (anyio cancel scopes require it); the job thread's calls
    run as further tasks on that loop through ``run_coroutine_threadsafe``."""

    def __init__(self, open_client: Callable[[], Any], timeout: float):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="facet-mcp-session", daemon=True)
        self.thread.start()
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        self._stop: asyncio.Event | None = None
        self._main = asyncio.run_coroutine_threadsafe(self._hold(open_client), self.loop)
        try:
            self.client = self._ready.result(timeout=timeout)
        except BaseException:
            self._shutdown()
            raise

    async def _hold(self, open_client):
        self._stop = asyncio.Event()
        try:
            async with open_client() as c:
                self._ready.set_result(c)
                await self._stop.wait()
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            raise

    def run(self, op):
        return asyncio.run_coroutine_threadsafe(op(self.client), self.loop).result()

    def close(self) -> None:
        if self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)
        try:
            self._main.result(timeout=10)
        except BaseException:   # closing a broken session: nothing left to save
            pass
        self._shutdown()

    def _shutdown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        if not self.thread.is_alive():
            self.loop.close()


def _http_transport(url: str, token: str, timeout: float):
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout, trust_env=False)
    return streamable_http_client(url, http_client=http)


class McpFacetClient:
    """Facet through its MCP endpoint with an add-on token."""

    def __init__(self, url: str, *, token: str, timeout: float = 60.0, session: requests.Session | None = None,
                 transport: Callable[[], Any] | None = None, sleep: Callable[[float], None] = time.sleep):
        if not token:
            raise ValueError("an MCP client needs FACET_TOKEN")
        self.url = url.rstrip("/")
        self.static_token = self.token = token
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.trust_env = False
        self._transport = transport or (lambda: _http_transport(self.url + MCP_PATH, token, timeout))
        self._tools: set[str] | None = None
        self._features: dict | None = None
        self._status: dict | None = None
        self.version: str | None = None
        self.mode = "mcp"
        self._sleep = sleep
        self._local = threading.local()     # per thread: job depth and its held session

    # ------------------------------------------------------------- transport ---
    def _open_client(self):
        from mcp import Client

        # "legacy": initialize straight away (Facet's stateless endpoint speaks 2025-11-25).
        return Client(self._transport(), read_timeout_seconds=self.timeout, mode="legacy")

    @contextmanager
    def job(self):
        """Keep one MCP session open for every call this thread makes inside the block (nested blocks share it).
        Opened on the first call, closed when the outermost block ends. Other threads are not affected."""
        loc = self._local
        loc.depth = getattr(loc, "depth", 0) + 1
        try:
            yield self
        finally:
            loc.depth -= 1
            if loc.depth == 0:
                self._drop_held()

    def _drop_held(self) -> None:
        held = getattr(self._local, "held", None)
        self._local.held = None
        if held is not None:
            held.close()

    def _session(self, op):
        try:
            if getattr(self._local, "depth", 0) > 0:
                held = getattr(self._local, "held", None)
                if held is None:
                    held = self._local.held = _HeldSession(self._open_client, self.timeout)
                try:
                    return held.run(op)
                except Exception:
                    self._drop_held()          # a broken session: the next call in this job opens a fresh one
                    raise

            async def main():
                async with self._open_client() as c:
                    return await op(c)
            return asyncio.run(main())
        except FacetError:
            raise
        except Exception as exc:   # transport, HTTP status, protocol: one error type for the callers
            raise FacetError(f"Facet MCP at {self.url}{MCP_PATH}: {_reason(exc)}", _status(exc)) from None

    def call(self, tool: str, args: dict | None = None) -> Any:
        """One tool call; its ``data`` on success, :class:`FacetError` on a refusal (status from the error code).
        ``rate_limited`` is retried after Facet's ``retry_after_s`` (at most :data:`RATE_RETRIES` times)."""
        async def op(c):
            return await c.call_tool(tool, args or {})
        for attempt in range(RATE_RETRIES + 1):
            result = self._session(op)
            text = next((b.text for b in reversed(result.content or []) if getattr(b, "type", "") == "text"), "")
            try:
                env = json.loads(text)
            except ValueError:
                raise FacetError(f"{tool}: not a Facet envelope: {text[:200]}", 502) from None
            if env.get("ok"):
                return env.get("data")
            code = env.get("error") or "error"
            if code == "rate_limited" and attempt < RATE_RETRIES:
                wait = env.get("retry_after_s")
                wait = float(wait) if isinstance(wait, (int, float)) and wait > 0 else 2.0 ** (attempt + 1)
                log.info("%s: rate limited by Facet, retrying in %.0f s", tool, min(wait, MAX_RETRY_WAIT_S))
                self._sleep(min(wait, MAX_RETRY_WAIT_S))
                continue
            raise FacetError(f"{tool}: {code}: {env.get('message', '')}"[:400], _STATUS.get(code, 502), code=code,
                             issues=env.get("issues"))
        raise AssertionError("unreachable")

    def tools(self) -> set[str]:
        """The tools this token may call (``tools/list``), cached."""
        if self._tools is None:
            async def op(c):
                return await c.list_tools()
            self._tools = {t.name for t in self._session(op).tools}
        return self._tools

    # ---------------------------------------------------------------- auth ---
    @property
    def can_login(self) -> bool:
        return True

    @property
    def scoped(self) -> bool:
        return True

    def login(self) -> str:
        return self.token

    # ------------------------------------------------------------ discovery ---
    def status(self) -> dict:
        """The ``status`` tool's answer (``photos.read``), cached; ``{}`` when it is refused."""
        if self._status is None:
            try:
                self._status = dict(self.call("status") or {})
            except FacetError as exc:
                log.warning("Facet status unknown (%s)", exc)
                self._status = {}
        return self._status

    def server_version(self) -> str | None:
        """Facet's version, from the ``status`` tool (``photos.read``)."""
        if self.version is None:
            self.version = str(self.status().get("server_version") or "unknown")
        return self.version

    def features(self) -> dict:
        """What this token can do: the tools off ``tools/list``, and whether Facet speaks the model D contract
        (``status`` reports ``features`` for an add-on token, ``annotations`` among them). No probing requests."""
        if self._features is None:
            t = self.tools()
            st = self.status()
            facet_feats = st.get("features")
            model_d = isinstance(facet_feats, list) and "annotations" in facet_feats
            tags = {"add_tags", "remove_tags"} <= t
            self._features = {"annotations_put": "set_annotations" in t, "annotations_read": "get_annotations" in t,
                              "plugin_tags": model_d and tags, "tags": tags, "gps": "set_gps" in t,
                              "model_d": model_d,
                              "annotations_batch": model_d and "set_annotations_batch" in t,
                              "annotation_summary": model_d and "get_annotations" in t,
                              "facet_features": list(facet_feats) if isinstance(facet_feats, list) else []}
        return self._features

    # ---------------------------------------------------------------- reads ---
    def list_photos(self, *, path_prefix: str | None = None, category: str | None = None, tag: str | None = None,
                    album_id: int | str | None = None, date_from: str | None = None, limit: int | None = None,
                    extra: dict | None = None) -> list[dict]:
        """Every matching photo (all pages), every frame of every set. ``extra`` keys Facet does not take as a
        ``search_photos`` filter are dropped (once, with a warning) rather than failing the whole listing."""
        filters: dict = dict(ALL_FRAMES)
        for k, v in (("path_prefix", path_prefix), ("category", category), ("tag", tag), ("album_id", album_id),
                     ("date_from", date_from)):
            if v not in (None, ""):
                filters[k] = str(v)
        for k, v in (extra or {}).items():
            if isinstance(v, (list, tuple)):
                v = ",".join(str(x) for x in v)
            filters[k] = v if isinstance(v, (bool, int, float)) else str(v)
        if "scan_run" in filters and str(filters["scan_run"]).isdigit():
            filters["scan_run"] = int(filters["scan_run"])
        out: list[dict] = []
        page = 1
        while True:
            args = {"filters": filters, "sort": "date_taken", "sort_direction": "asc", "page": page,
                    "per_page": PAGE_SIZE, "fields": ROW_FIELDS, "show": False}
            try:
                data = self.call("search_photos", args)
            except FacetError as exc:
                bad = _bad_filters(exc)
                if not bad:
                    raise
                log.warning("Facet does not take these search filters, dropped: %s", ", ".join(sorted(bad)))
                filters = {k: v for k, v in filters.items() if k not in bad}
                continue
            rows = (data or {}).get("rows") or []
            out.extend(rows)
            if limit and len(out) >= limit:
                return out[:limit]
            more = (data or {}).get("has_more")
            if not rows or (more is False) or (more is None and len(rows) < PAGE_SIZE):
                return out
            page += 1

    def photo(self, path: str) -> dict | None:
        try:
            return self.call("get_photo", {"path": path})
        except FacetError as exc:
            if exc.status == 404:
                return None
            raise

    def albums(self) -> list[dict]:
        data = self.call("list_albums", {}) or {}
        return data.get("rows") or []

    def thumbnail(self, path: str, size: int | None = None) -> tuple[bytes, str]:
        """(bytes, content type) of Facet's thumbnail: ``GET /thumbnail`` stays open to an add-on token (open
        question (g): MCP has no byte channel yet)."""
        params = {"path": path}
        if size:
            params["size"] = str(int(size))
        try:
            r = self.http.get(self.url + "/thumbnail", params=params, timeout=self.timeout,
                              headers={"Accept": "image/*", "Authorization": f"Bearer {self.token}"})
        except requests.RequestException as exc:
            raise FacetError(f"cannot reach Facet at {self.url}: {exc}") from None
        if r.status_code != 200:
            raise FacetError(f"GET /thumbnail: HTTP {r.status_code}", r.status_code)
        ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not ctype.startswith("image/"):
            raise FacetError(f"GET /thumbnail: not an image ({ctype})", 502)
        return r.content, ctype

    def annotations(self, path: str) -> list[dict]:
        """Every source's overlay for one photo (``get_annotations``, Facet PR-1)."""
        return (self.call("get_annotations", {"path": path}) or {}).get("annotations") or []

    def annotations_summary(self, path: str) -> list[dict]:
        """This add-on's overlay of one photo without geometry (model D ``get_annotations`` ``summary: true``):
        ``[{source, version, title, legend, image, groups (each with count), metrics, score, items, indexed}]``."""
        return (self.call("get_annotations", {"path": path, "summary": True}) or {}).get("summary") or []

    # --------------------------------------------------------------- writes ---
    def batch_add_tag(self, tag: str, paths: list[str], plugin: bool = False) -> int:
        """Add one tag to ``paths``; ``plugin=True`` (model D) writes it as this add-on's summary tag."""
        n = 0
        extra = {"plugin": True} if plugin else {}
        for i in range(0, len(paths), TAG_BATCH):
            n += int((self.call("add_tags", {"tags": [tag], "paths": paths[i:i + TAG_BATCH], **extra}) or {})
                     .get("affected") or 0)
        return n

    def delete_tag(self, path: str, tag: str) -> bool:
        return bool((self.call("remove_tags", {"tags": [tag], "paths": [path]}) or {}).get("affected"))

    def batch_remove_tag(self, tag: str, paths: list[str]) -> dict:
        """Remove one tag from ``paths`` (a model D Facet removes only this add-on's rows):
        ``{affected, skipped_foreign}``."""
        out = {"affected": 0, "skipped_foreign": 0}
        for i in range(0, len(paths), TAG_BATCH):
            d = self.call("remove_tags", {"tags": [tag], "paths": paths[i:i + TAG_BATCH]}) or {}
            out["affected"] += int(d.get("affected") or 0)
            out["skipped_foreign"] += int(d.get("skipped_foreign") or 0)
        return out

    def set_location(self, lat: float, lon: float, *, path_prefix: str | None = None, album_id: int | None = None,
                     overwrite: bool = False, dry_run: bool = False) -> dict:
        """One location for a whole folder or album (R-LOC): ``{changed, skipped, ...}``. Never per photo."""
        if (path_prefix is None) == (album_id is None):
            raise ValueError("a location is set for a folder or an album, exactly one")
        args: dict = {"latitude": lat, "longitude": lon, "overwrite": bool(overwrite), "dry_run": bool(dry_run)}
        if path_prefix is not None:
            args["path_prefix"] = path_prefix
        else:
            args["album_id"] = int(album_id)
        return self.call("set_gps", args) or {}

    def put_annotation(self, path: str, source: str, payload: dict) -> dict:
        """Store this add-on's overlay (``set_annotations``); Facet forces the source to the add-on id."""
        return self.call("set_annotations", {"path": path, "payload": payload}) or {}

    def put_annotations_batch(self, payloads: dict[str, dict]) -> dict[str, dict]:
        """Store many overlays (model D ``set_annotations_batch``), in chunks of at most :data:`ANN_BATCH` photos
        and :data:`ANN_BATCH_BYTES`, one write each. ``{path: {ok, indexed, index_skipped} | {ok: False, error,
        message}}``."""
        out: dict[str, dict] = {}
        chunk: dict[str, dict] = {}
        size = 0

        def flush():
            nonlocal chunk, size
            if not chunk:
                return
            d = self.call("set_annotations_batch", {"payloads": chunk}) or {}
            for r in d.get("results") or []:
                if isinstance(r, dict) and r.get("path") in chunk:
                    out[r["path"]] = r
            for p in chunk:
                out.setdefault(p, {"path": p, "ok": False, "error": "unknown", "message": "no result for this photo"})
            chunk, size = {}, 0

        for path, payload in payloads.items():
            n = len(json.dumps({path: payload}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if chunk and (len(chunk) >= ANN_BATCH or size + n > ANN_BATCH_BYTES):
                flush()
            chunk[path] = payload
            size += n
        flush()
        return out

    def delete_annotation(self, path: str, source: str) -> bool:
        return bool((self.call("clear_annotations", {"path": path}) or {}).get("removed"))

    def photo_page_url(self, path: str) -> str:
        return f"{self.url}/photo?path={quote(path, safe='')}"


def _bad_filters(exc: FacetError) -> set[str]:
    """Filter names an ``invalid_input`` refusal blames (``filters.<key>``), if that is all it blames."""
    if getattr(exc, "code", None) != "invalid_input":
        return set()
    bad = set()
    for issue in getattr(exc, "issues", None) or []:
        field = str(issue.get("field") or "")
        parts = field.split(".")
        if len(parts) < 2 or parts[0] != "filters":
            return set()
        bad.add(parts[1])
    return bad


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for e in exc.exceptions for leaf in _leaves(e)]
    return [exc]


def _reason(exc: BaseException) -> str:
    return "; ".join(f"{type(e).__name__}: {e}"[:200] for e in _leaves(exc))


def _status(exc: BaseException) -> int | None:
    for e in _leaves(exc):
        resp = getattr(e, "response", None)
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
        text = str(e)
        for c in (401, 403, 404):
            if f"{c}" in text and ("HTTP" in text or "status" in text.lower()):
                return c
    return None
