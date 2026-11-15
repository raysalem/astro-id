"""A stub of Facet's MCP endpoint (PLUGIN_BOUNDARY.md v0.3 §6a) for :class:`fake_facet.FakeFacet`.

A real ``mcp`` SDK server (streamable HTTP, stateless, JSON responses, like Facet's) on its own loopback port; the
fake's ``/mcp`` route checks the bearer and forwards to it. Tools act on the fake's photos with Facet's envelope
``{ok, data}`` / ``{ok: false, error, message[, issues]}``. ``tools`` is the add-on's allowlist: ``tools/list``
shows only those, and a call to any other answers ``forbidden_level`` (as Facet does for an add-on token).

``fake.model_d`` switches on the add-on contract of Facet fix/review-all (docs/CONFIGURATION.md "Add-ons"):
``status`` reports ``facet_api``/``annotation_payload``/``annotation_shapes``/``features``; ``add_tags`` takes
``plugin: true`` (summary tags, at most 3 per call and 3 per photo per add-on, listed in ``plugin_tags``);
``remove_tags`` deletes only the add-on's own rows and answers ``skipped_foreign``; ``set_annotations`` /
``set_annotations_batch`` copy items marked ``index`` (or in ``index_groups``) into ``fake.detections`` (labels
normalized like tags, comma or > 64 chars skipped, at most 200, highest score kept) and answer ``indexed`` /
``index_skipped``; ``get_annotations`` takes ``summary: true``; ``clear_annotations`` clears the detections too.
Without ``model_d`` it is the older Facet (local/addons-annot): ``status`` carries none of that, ``add_tags`` has no
``plugin`` parameter in ``tools/list`` and silently IGNORES a ``plugin`` argument (writes a plain row), as the real
MCP SDK does.
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
# Model D adds these for annotations.write.
MODEL_D_TOOLS = frozenset({"set_annotations_batch", "get_detections"})
KNOWN = ASTRO_TOOLS | ANNOTATION_TOOLS | MODEL_D_TOOLS | {"delete_photos", "set_rating"}
MODEL_D_STATUS = {"facet_api": 1, "annotation_payload": [1], "annotation_shapes": ["point", "circle", "lines",
                  "rect", "polygon"], "features": ["annotations", "annotation_view", "import_hook", "sidebar",
                                                   "context_locale", "proxy_secret", "rate_profile"]}
PLUGIN_CAP = 3
MAX_DETECTIONS = 200


def _norm(label):
    """db.manual_tags.normalize_manual_tag, enough of it for the stub."""
    if not isinstance(label, str):
        return None
    t = " ".join(label.split()).lower()
    return t if t and "," not in t and len(t) <= 64 else None


def index_items(payload: dict) -> tuple[list[dict], int]:
    """(detections, skipped) the way Facet derives them from a v1 payload."""
    groups = set(payload.get("index_groups") or ())
    out, skipped = [], 0
    for it in payload.get("items") or []:
        flag = it.get("index")
        if not (flag is True or (flag is None and it.get("group") in groups)):
            continue
        label = _norm(it.get("label"))
        if label is None:
            skipped += 1
            continue
        out.append({"label": label, "grp": it.get("group"), "score": it.get("score")})
    out.sort(key=lambda d: -(d["score"] or 0))
    if len(out) > MAX_DETECTIONS:
        skipped += len(out) - MAX_DETECTIONS
        out = out[:MAX_DETECTIONS]
    return out, skipped
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
            tools = [t for t in await super().list_tools() if t.name in fake.mcp_tools]
            if not fake.model_d:      # the older Facet's add_tags has no `plugin` parameter
                for t in tools:
                    props = (getattr(t, "input_schema", None) or {}).get("properties")
                    if t.name == "add_tags" and isinstance(props, dict):
                        props.pop("plugin", None)
            return tools

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
        doc = {"owner": "_legacy", "level": "edition", "server_version": fake.version}
        if fake.model_d:
            doc.update(MODEL_D_STATUS)
        return guard("status", {}) or _env(doc)

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
        if not r:
            return _env(error="not_found", message="Photo not found")
        v = view(r)
        if fake.model_d:
            v["plugin_tags"] = [{"tag": t, "source": fake.addon_id} for t in sorted(r.get("plugin_rows") or ())]
        if fake.tag_sources:     # gallery.py _addon_tag_sources_async: only rows an installed add-on wrote
            owners = r.get("tag_created_by") or {}
            v["manual_tag_sources"] = {t: owners[t] for t in r["manual_tags"] if owners.get(t) == fake.addon_id}
        return _env(v)

    async def filter_options(kind: str = "categories") -> str:
        return guard("filter_options", {"kind": kind}) or _env({"rows": []})

    async def list_albums(query: str = "", show: bool = False) -> str:
        if (refused := guard("list_albums", {})) is not None:
            return refused
        ids = sorted({a for r in fake.photos.values() for a in r.get("albums", ())})
        return _env({"rows": [{"id": i, "name": fake.album_names.get(i, f"Album {i}"),
                               "photo_count": sum(1 for r in fake.photos.values() if i in r.get("albums", ()))}
                              for i in ids], "total": len(ids)})

    def tag_write(name, tags, paths, add, plugin=False):
        args = {"tags": tags, "paths": paths, **({"plugin": True} if plugin else {})}
        if (refused := guard(name, args)) is not None:
            return refused
        if plugin and not fake.model_d:
            plugin = False       # the old Facet ignores the unknown argument (pydantic extra="ignore") and writes a
                                 # plain row; the call is still recorded above with plugin: true
        if plugin and len(tags) > PLUGIN_CAP:
            return _env(error="too_large", message=f"at most {PLUGIN_CAP} plugin tags per call")
        n = 0
        foreign = 0
        for tag in tags:
            for path in paths:
                r = fake.photos.get(path)
                if r is None:
                    continue
                ai = {t.strip() for t in (r.get("tags") or "").split(",") if t.strip()}
                owners = r.setdefault("tag_created_by", {})
                rows = r.setdefault("plugin_rows", set())
                if add and plugin:
                    if tag in r["manual_tags"] or tag in ai or len(rows) >= PLUGIN_CAP:
                        continue
                    r["manual_tags"] = sorted(r["manual_tags"] + [tag])
                    owners[tag] = fake.addon_id
                    rows.add(tag)
                    n += 1
                elif add:
                    user_rows = [t for t in r["manual_tags"] if t not in rows]
                    if tag in r["manual_tags"] or tag in ai or len(user_rows) >= CAP:
                        continue
                    r["manual_tags"] = sorted(r["manual_tags"] + [tag])
                    owners[tag] = fake.addon_id
                    n += 1
                elif tag in r["manual_tags"]:
                    if fake.model_d and owners.get(tag) != fake.addon_id:
                        foreign += 1            # someone else's row: left in place
                        continue
                    r["manual_tags"].remove(tag)
                    owners.pop(tag, None)
                    rows.discard(tag)
                    n += 1
        out = {"affected": n}
        if fake.model_d and not add:
            out["skipped_foreign"] = foreign
        return _env(out)

    async def add_tags(tags: list[str], paths: list[str] | None = None, plugin: bool = False) -> str:
        return tag_write("add_tags", tags, paths or [], True, plugin)

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
    def summary(source, pl):
        items = pl.get("items") or []
        counts = {}
        for it in items:
            counts[it.get("group")] = counts.get(it.get("group"), 0) + 1
        idx, _ = index_items(pl)
        return {"source": source, "version": pl.get("version"), "title": pl.get("title"), "legend": pl.get("legend"),
                "image": pl.get("image"), "metrics": pl.get("metrics") or [], "score": pl.get("score"),
                "groups": [{**{k: g.get(k) for k in ("id", "label", "default") if k in g},
                            "count": counts.get(g.get("id"), 0)} for g in pl.get("groups") or []],
                "items": len(items), "indexed": len(idx)}

    async def get_annotations(path: str, source: str | None = None, summary: bool = False) -> str:
        if (refused := guard("get_annotations", {"path": path, "summary": summary})) is not None:
            return refused
        rows = [(s_, pl) for (pp, s_), pl in sorted(fake.annotations.items()) if pp == path]
        if summary and fake.model_d:
            return _env({"path": path, "summary": [summary_of(s_, pl) for s_, pl in rows]})
        return _env({"path": path, "annotations": [{"source": s_, "payload": pl} for s_, pl in rows]})

    summary_of = summary

    def store(path, payload):
        fake.annotations[(path, "astro-id")] = payload
        if not fake.model_d:
            return {"path": path, "source": "astro-id", "items": len(payload.get("items") or [])}
        det, skipped = index_items(payload)
        fake.detections[path] = det
        return {"path": path, "source": "astro-id", "items": len(payload.get("items") or []),
                "indexed": len(det), "index_skipped": skipped}

    async def set_annotations(path: str, payload: dict, source: str | None = None) -> str:
        if (refused := guard("set_annotations", {"path": path, "source": source})) is not None:
            return refused
        if source not in (None, "astro-id"):
            return _env(error="invalid_input", message="An add-on writes only its own overlay")
        return _env(store(path, payload))

    async def set_annotations_batch(payloads: dict, source: str | None = None) -> str:
        if (refused := guard("set_annotations_batch", {"paths": sorted(payloads)})) is not None:
            return refused
        if not 1 <= len(payloads) <= 200:
            return _env(error="invalid_input", message="1..200 payloads")
        results = []
        for path, pl in payloads.items():
            if path not in fake.photos:
                results.append({"path": path, "ok": False, "error": "not_found", "message": "Photo not found"})
                continue
            r = store(path, pl)
            results.append({"path": path, "ok": True, "indexed": r["indexed"], "index_skipped": r["index_skipped"]})
        return _env({"source": "astro-id", "written": sum(1 for r in results if r["ok"]),
                     "failed": sum(1 for r in results if not r["ok"]), "results": results})

    async def get_detections(path: str, source: str | None = None) -> str:
        if (refused := guard("get_detections", {"path": path})) is not None:
            return refused
        return _env({"path": path, "detections": [{"source": "astro-id", **d} for d in fake.detections.get(path, [])]})

    async def clear_annotations(path: str, source: str | None = None) -> str:
        if (refused := guard("clear_annotations", {"path": path})) is not None:
            return refused
        removed = fake.annotations.pop((path, "astro-id"), None) is not None
        fake.detections.pop(path, None)
        return _env({"path": path, "source": "astro-id", "removed": removed})

    async def delete_photos(paths: list[str]) -> str:
        return guard("delete_photos", {"paths": paths}) or _env({"deleted": []})

    async def set_rating(paths: list[str], rating: int) -> str:
        return guard("set_rating", {"paths": paths}) or _env({"affected": 0})

    for fn in (status, search_photos, get_photo, filter_options, list_albums, add_tags, remove_tags, set_gps,
               get_annotations, set_annotations, set_annotations_batch, get_detections, clear_annotations,
               delete_photos, set_rating):
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
