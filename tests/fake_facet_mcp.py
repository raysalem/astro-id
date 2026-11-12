"""A stub of Facet's MCP endpoint (PLUGIN_BOUNDARY.md v0.3 §6a) for :class:`fake_facet.FakeFacet`.

A real ``mcp`` SDK server (streamable HTTP, stateless, JSON responses, like Facet's) on its own loopback port; the
fake's ``/mcp`` route checks the bearer and forwards to it. Tools act on the fake's photos with Facet's envelope
``{ok, data}`` / ``{ok: false, error, message[, issues]}``. ``tools`` is the add-on's allowlist: ``tools/list``
shows only those, and a call to any other answers ``forbidden_level`` (as Facet does for an add-on token).
"""
from __future__ import annotations

import json
import threading
import time

ASTRO_TOOLS = frozenset({"status", "search_photos", "get_photo", "filter_options", "list_albums",
                         "add_tags", "remove_tags", "set_gps"})
# Every tool the stub knows; the allowlist picks from these. delete_photos exists only to be refused.
# Facet PR-1 annotation tools: off by default (a Facet without PR-1), on with mcp_tools=ASTRO_TOOLS | ANNOTATION_TOOLS.
ANNOTATION_TOOLS = frozenset({"get_annotations", "set_annotations", "clear_annotations"})
KNOWN = ASTRO_TOOLS | ANNOTATION_TOOLS | {"delete_photos", "set_rating"}
FILTER_KEYS = {"path_prefix", "scan_run", "tag", "category", "date_from", "album_id", "hide_blinks", "hide_bursts",
               "hide_duplicates", "hide_brackets", "hide_panoramas", "hide_rejected"}
COMPACT = ("path", "filename", "date_taken", "category")
CAP = 50


def _env(data=None, error=None, message="", **extra) -> str:
    doc = {"ok": True, "data": data} if error is None else {"ok": False, "error": error, "message": message, **extra}
    return json.dumps(doc)


def _folder_match(path: str, prefix: str) -> bool:
    return path.replace("\\", "/").startswith(prefix.replace("\\", "/").rstrip("/") + "/")


def build_server(fake):
    from mcp.server.mcpserver import MCPServer

    class Stub(MCPServer):
        async def list_tools(self):
            return [t for t in await super().list_tools() if t.name in fake.mcp_tools]

    server = Stub("facet")

    def guard(name: str, args: dict):
        fake.mcp_calls.append((name, args))
        if name not in fake.mcp_tools:
            return _env(error="forbidden_level", message=f"This add-on's permissions do not include {name}")
        return None

    def view(r):
        return fake.view(r)

    def select(filters: dict):
        rows = list(fake.photos.values())
        if filters.get("path_prefix"):
            rows = [r for r in rows if _folder_match(r["path"], str(filters["path_prefix"]))]
        if filters.get("scan_run") not in (None, ""):
            rows = [r for r in rows if str(r.get("scan_run")) == str(filters["scan_run"])]
        if filters.get("tag"):
            rows = [r for r in rows if filters["tag"] in r["manual_tags"]]
        if filters.get("category"):
            rows = [r for r in rows if r.get("category") == filters["category"]]
        if filters.get("album_id") not in (None, ""):
            rows = [r for r in rows if int(filters["album_id"]) in r.get("albums", ())]
        if filters.get("date_from"):
            rows = [r for r in rows if (r.get("date_taken") or "").replace(":", "-", 2) >= filters["date_from"]]
        return rows

    async def status() -> str:
        return guard("status", {}) or _env({"owner": "_legacy", "level": "edition", "server_version": fake.version})

    async def search_photos(filters: dict | None = None, sort: str | None = None, sort_direction: str | None = None,
                            page: int = 1, per_page: int = 50, fields: list[str] | None = None,
                            show: bool = True) -> str:
        args = {"filters": filters, "page": page, "per_page": per_page, "fields": fields, "show": show}
        if (refused := guard("search_photos", args)) is not None:
            return refused
        filters = filters or {}
        bad = sorted(k for k in filters if k not in FILTER_KEYS)
        if bad:
            return _env(error="invalid_input", message="Invalid arguments",
                        issues=[{"field": f"filters.{k}", "message": "Extra inputs are not permitted"} for k in bad])
        if per_page > 100:
            return _env(error="invalid_input", message="per_page <= 100", issues=[{"field": "per_page"}])
        rows = select(filters)
        chunk = rows[(page - 1) * per_page: page * per_page]
        out = []
        for r in chunk:
            row = {k: r.get(k) for k in COMPACT}
            row["tags"] = view(r)["tags_list"][:5]
            for f in fields or ():
                row.setdefault(f, r.get(f))
            out.append(row)
        return _env({"rows": out, "total": len(rows), "page": page, "per_page": per_page,
                     "has_more": page * per_page < len(rows)})

    async def get_photo(path: str) -> str:
        if (refused := guard("get_photo", {"path": path})) is not None:
            return refused
        r = fake.photos.get(path)
        return _env(view(r)) if r else _env(error="not_found", message="Photo not found")

    async def filter_options(kind: str = "categories") -> str:
        return guard("filter_options", {"kind": kind}) or _env({"rows": []})

    async def list_albums(query: str = "", show: bool = False) -> str:
        if (refused := guard("list_albums", {})) is not None:
            return refused
        ids = sorted({a for r in fake.photos.values() for a in r.get("albums", ())})
        return _env({"rows": [{"id": i, "name": fake.album_names.get(i, f"Album {i}"),
                               "photo_count": sum(1 for r in fake.photos.values() if i in r.get("albums", ()))}
                              for i in ids], "total": len(ids)})

    def tag_write(name, tags, paths, add):
        if (refused := guard(name, {"tags": tags, "paths": paths})) is not None:
            return refused
        n = 0
        for tag in tags:
            for path in paths:
                r = fake.photos.get(path)
                if r is None:
                    continue
                ai = {t.strip() for t in (r.get("tags") or "").split(",") if t.strip()}
                if add:
                    if tag in r["manual_tags"] or tag in ai or len(r["manual_tags"]) >= CAP:
                        continue
                    r["manual_tags"] = sorted(r["manual_tags"] + [tag])
                    r.setdefault("tag_created_by", {})[tag] = fake.addon_id
                    n += 1
                elif tag in r["manual_tags"]:
                    r["manual_tags"].remove(tag)
                    n += 1
        return _env({"affected": n})

    async def add_tags(tags: list[str], paths: list[str] | None = None) -> str:
        return tag_write("add_tags", tags, paths or [], True)

    async def remove_tags(tags: list[str], paths: list[str] | None = None) -> str:
        return tag_write("remove_tags", tags, paths or [], False)

    async def set_gps(latitude: float, longitude: float, path_prefix: str | None = None, album_id: int | None = None,
                      overwrite: bool = False, dry_run: bool = False) -> str:
        args = {"latitude": latitude, "longitude": longitude, "path_prefix": path_prefix, "album_id": album_id,
                "overwrite": overwrite, "dry_run": dry_run}
        if (refused := guard("set_gps", args)) is not None:
            return refused
        if (path_prefix is None) == (album_id is None):
            return _env(error="invalid_input", message="Pass exactly one of path_prefix or album_id")
        rows = select({"path_prefix": path_prefix} if path_prefix is not None else {"album_id": album_id})
        changed = [r for r in rows if (r.get("gps_latitude"), r.get("gps_longitude")) != (latitude, longitude)
                   and (overwrite or r.get("gps_latitude") is None)]
        if not dry_run:
            for r in changed:
                r.update(gps_latitude=latitude, gps_longitude=longitude, gps_manually_set=1)
        scope = {"path_prefix": path_prefix} if path_prefix is not None else {"album_id": album_id}
        return _env({"scope": scope, "latitude": latitude, "longitude": longitude, "overwrite": overwrite,
                     "changed": len(changed), "skipped": len(rows) - len(changed), "dry_run": dry_run})

    # PR-1: the source is always the add-on id (Facet forces it), here "astro-id".
    async def get_annotations(path: str) -> str:
        if (refused := guard("get_annotations", {"path": path})) is not None:
            return refused
        return _env({"path": path, "annotations": [{"source": s, "payload": pl} for (pp, s), pl
                                                   in sorted(fake.annotations.items()) if pp == path]})

    async def set_annotations(path: str, payload: dict, source: str | None = None) -> str:
        if (refused := guard("set_annotations", {"path": path, "source": source})) is not None:
            return refused
        if source not in (None, "astro-id"):
            return _env(error="invalid_input", message="An add-on writes only its own overlay")
        fake.annotations[(path, "astro-id")] = payload
        return _env({"path": path, "source": "astro-id", "items": len(payload.get("items") or [])})

    async def clear_annotations(path: str, source: str | None = None) -> str:
        if (refused := guard("clear_annotations", {"path": path})) is not None:
            return refused
        removed = fake.annotations.pop((path, "astro-id"), None) is not None
        return _env({"path": path, "source": "astro-id", "removed": removed})

    async def delete_photos(paths: list[str]) -> str:
        return guard("delete_photos", {"paths": paths}) or _env({"deleted": []})

    async def set_rating(paths: list[str], rating: int) -> str:
        return guard("set_rating", {"paths": paths}) or _env({"affected": 0})

    for fn in (status, search_photos, get_photo, filter_options, list_albums, add_tags, remove_tags, set_gps,
               get_annotations, set_annotations, clear_annotations, delete_photos, set_rating):
        server.add_tool(fn, name=fn.__name__, structured_output=False)
    return server


class StubMcp:
    """The stub server on its own port (uvicorn in a thread)."""

    def __init__(self, fake):
        import uvicorn
        from mcp.server.transport_security import TransportSecuritySettings

        self.server_obj = build_server(fake)
        app = self.server_obj.streamable_http_app(
            streamable_http_path="/mcp", json_response=True, stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        self.uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.uv.run, daemon=True)

    def start(self) -> int:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.uv.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.uv.started, "stub MCP server did not start"
        return self.uv.servers[0].sockets[0].getsockname()[1]

    def stop(self) -> None:
        self.uv.should_exit = True
        self.thread.join(timeout=10)


def forward(port: int, method: str, path: str, headers, body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    import http.client

    keep = {k: v for k, v in headers.items() if k.lower() in ("content-type", "accept", "mcp-protocol-version",
                                                             "mcp-session-id", "last-event-id")}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request(method, path, body=body or None, headers=keep)
    r = c.getresponse()
    data = r.read()
    hdrs = [(k, v) for k, v in r.getheaders() if k.lower() not in ("content-length", "transfer-encoding",
                                                                     "connection", "date", "server")]
    c.close()
    return r.status, hdrs, data
