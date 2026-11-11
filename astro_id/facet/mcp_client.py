"""Facet over MCP: the add-on's data channel (PLUGIN_BOUNDARY.md v0.3 §6a, R-MCP).

An add-on token (``FACET_TOKEN``, ``fct_...``) opens Facet's ``POST /mcp`` (streamable HTTP, stateless) and is refused
on ``/api`` except for the image bytes (``GET /thumbnail``). The tools it sees are the ones its permissions grant:

- ``photos.read``: ``status``, ``search_photos`` (with the ``scan_run`` filter), ``get_photo``, ``filter_options``,
  ``list_albums``;
- ``tags.write``: ``add_tags``, ``remove_tags`` (Facet records the add-on as ``created_by``);
- ``gps.write``: ``set_gps``, for a whole folder or album, never one photo (R-LOC);
- ``annotations.write``: nothing until Facet PR-1.

:class:`McpFacetClient` has the same surface as the ``/api`` :class:`~astro_id.facet.client.FacetClient`, so the
sync and session code does not care which one it holds. Each call opens one short MCP connection (``asyncio.run``):
the server is stateless and the add-on backend is a threaded HTTP server, so nothing is shared between threads.
"""
from __future__ import annotations

import asyncio
import json
import logging
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
# Every frame of a set (the viewer hides bursts, duplicates ... by default; a solver wants all of them).
ALL_FRAMES = {"hide_blinks": False, "hide_bursts": False, "hide_duplicates": False, "hide_brackets": False,
              "hide_panoramas": False, "hide_rejected": False}
# Columns the session needs beyond the compact search row.
ROW_FIELDS = ["gps_latitude", "gps_longitude", "gps_manually_set", "date_manually_set"]
_STATUS = {"forbidden_level": 403, "feature_disabled": 403, "not_found": 404, "invalid_input": 422,
           "too_large": 413, "conflict": 409, "rate_limited": 429}


def _http_transport(url: str, token: str, timeout: float):
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout, trust_env=False)
    return streamable_http_client(url, http_client=http)


class McpFacetClient:
    """Facet through its MCP endpoint with an add-on token."""

    def __init__(self, url: str, *, token: str, timeout: float = 60.0, session: requests.Session | None = None,
                 transport: Callable[[], Any] | None = None):
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
        self.version: str | None = None
        self.mode = "mcp"

    # ------------------------------------------------------------- transport ---
    def _session(self, op):
        from mcp import Client

        async def main():
            # "legacy": initialize straight away (Facet's stateless endpoint speaks 2025-11-25).
            async with Client(self._transport(), read_timeout_seconds=self.timeout, mode="legacy") as c:
                return await op(c)
        try:
            return asyncio.run(main())
        except FacetError:
            raise
        except Exception as exc:   # transport, HTTP status, protocol: one error type for the callers
            raise FacetError(f"Facet MCP at {self.url}{MCP_PATH}: {_reason(exc)}", _status(exc)) from None

    def call(self, tool: str, args: dict | None = None) -> Any:
        """One tool call; its ``data`` on success, :class:`FacetError` on a refusal (status from the error code)."""
        async def op(c):
            return await c.call_tool(tool, args or {})
        result = self._session(op)
        text = next((b.text for b in reversed(result.content or []) if getattr(b, "type", "") == "text"), "")
        try:
            env = json.loads(text)
        except ValueError:
            raise FacetError(f"{tool}: not a Facet envelope: {text[:200]}", 502) from None
        if not env.get("ok"):
            code = env.get("error") or "error"
            raise FacetError(f"{tool}: {code}: {env.get('message', '')}"[:400], _STATUS.get(code, 502), code=code,
                             issues=env.get("issues"))
        return env.get("data")

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
    def server_version(self) -> str | None:
        """Facet's version, from the ``status`` tool (``photos.read``)."""
        if self.version is None:
            try:
                self.version = str((self.call("status") or {}).get("server_version") or "unknown")
            except FacetError as exc:
                log.warning("Facet version unknown (%s)", exc)
                self.version = "unknown"
        return self.version

    def features(self) -> dict:
        """What this token can do, read off ``tools/list`` (no probing requests)."""
        if self._features is None:
            t = self.tools()
            self._features = {"annotations_put": "set_annotations" in t, "annotations_read": "get_annotations" in t,
                              "plugin_tags": False, "tags": {"add_tags", "remove_tags"} <= t, "gps": "set_gps" in t}
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
        raise FacetError("annotations are not on Facet's MCP yet (Facet PR-1)", 404)

    # --------------------------------------------------------------- writes ---
    def batch_add_tag(self, tag: str, paths: list[str]) -> int:
        n = 0
        for i in range(0, len(paths), TAG_BATCH):
            n += int((self.call("add_tags", {"tags": [tag], "paths": paths[i:i + TAG_BATCH]}) or {}).get("affected") or 0)
        return n

    def delete_tag(self, path: str, tag: str) -> bool:
        return bool((self.call("remove_tags", {"tags": [tag], "paths": [path]}) or {}).get("affected"))

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
        raise FacetError("annotations are not on Facet's MCP yet (Facet PR-1)", 404)

    def delete_annotation(self, path: str, source: str) -> bool:
        raise FacetError("annotations are not on Facet's MCP yet (Facet PR-1)", 404)

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
