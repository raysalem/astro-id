"""A small in-memory stand-in for the Facet viewer API (the routes the sidecar uses), on a real HTTP socket.

Behaviour copied from Facet 1.29 where the sidecar depends on it: the edition login returns a bearer token,
writes need it (401 without), ``batch_manual_tags`` add skips photos at the 50-tag cap or whose AI tags hold the
tag (INSERT OR IGNORE), delete removes the tag whatever its source, ``GET /api/photos`` pages with ``has_more``.
``annotations_put`` switches the PUT route on (absent = 405 like a FastAPI path without that method).

Add-on pieces (PLUGIN_BOUNDARY.md v0.2): ``GET /api/photos?scan_run=`` filters on a photo's ``scan_run`` field,
``GET /thumbnail`` returns a tiny JPEG, ``addon_token`` (``fct_...``) is accepted as a bearer and answers 403 on
any route in ``addon_denied`` (an add-on token outside its permissions), ``plugin_tags`` switches
``POST /api/photos/plugin_tags`` on (422 on an empty body, like FastAPI validation).

R-MCP (v0.3): with an ``addon_token`` the fake also serves ``POST /mcp`` (tests/fake_facet_mcp.py, a real ``mcp``
SDK server behind a bearer check); ``mcp_tools`` is the add-on's tool allowlist and ``mcp_calls`` records each call.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CAP = 50


class FakeFacet:
    def __init__(self, photos: list[dict], *, password="pw", annotations_put=True, annotations_read=True,
                 version="1.29.0", addon_token=None, addon_denied=(), plugin_tags=False, mcp_tools=None,
                 addon_id="astro-id", album_names=None, model_d=False):
        self.photos = {p["path"]: {"manual_tags": [], "tags": "", **p} for p in photos}
        self.password = password
        self.annotations_put = annotations_put
        self.annotations_read = annotations_read
        self.version = version
        self.annotations: dict = {}
        self.calls: list[tuple] = []
        self.tokens = {"tok-1"} | ({addon_token} if addon_token else set())
        self.addon_token = addon_token
        self.addon_denied = set(addon_denied)
        self.plugin_tags = plugin_tags
        self.expire_next = False
        self.logins = 0
        from fake_facet_mcp import ASTRO_TOOLS

        self.mcp_tools = set(ASTRO_TOOLS if mcp_tools is None else mcp_tools)
        self.mcp_calls: list[tuple] = []
        self.mcp_inits = 0                 # MCP initialize requests (one per session)
        self.model_d = model_d             # Facet fix/review-all add-on contract (fake_facet_mcp.py)
        self.detections: dict = {}         # model D: path -> [{label, grp, score}]
        self.addon_id = addon_id
        self.album_names = dict(album_names or {1: "Night"})
        self.mcp = None
        self.mcp_port = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        if self.addon_token:
            from fake_facet_mcp import StubMcp

            self.mcp = StubMcp(self)
            self.mcp_port = self.mcp.start()
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        if self.mcp is not None:
            self.mcp.stop()

    @staticmethod
    def view(r):
        v = dict(r)
        v["manual_tags"] = sorted(r["manual_tags"])
        v["tags_list"] = [t.strip() for t in (r.get("tags") or "").split(",") if t.strip()]
        v.pop("tag_created_by", None)
        v.pop("plugin_rows", None)
        v.pop("albums", None)
        return v

    def writes(self):
        """Calls that could change state (the feature probes, a PUT/POST with an empty body, cannot)."""
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE") and not c[1].startswith("/api/auth")
                and not (c[0] in ("PUT", "POST") and c[3] == {})]

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, code, doc):
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def _authed(self):
                tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                if fake.expire_next and tok:
                    fake.expire_next = False
                    fake.tokens.discard(tok)
                return tok in fake.tokens

            def handle_mcp(self, method):
                from fake_facet_mcp import forward

                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                fake.calls.append((method, "/mcp", {}, {}))
                try:
                    msg = json.loads(raw) if raw else None
                except ValueError:
                    msg = None
                if isinstance(msg, dict) and msg.get("method") == "initialize":
                    fake.mcp_inits += 1
                if fake.mcp is None or tok != fake.addon_token:
                    return self._out(401, {"detail": "A valid agent token is required"})
                status, hdrs, data = forward(fake.mcp_port, method, "/mcp", self.headers, raw)
                self.send_response(status)
                for k, v in hdrs:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None

            def handle_one(self, method):
                if urlparse(self.path).path == "/mcp":
                    return self.handle_mcp(method)
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                body = self._body() if method in ("POST", "PUT", "DELETE") else {}
                fake.calls.append((method, u.path, q, body))
                p = u.path
                tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                if fake.addon_token and tok == fake.addon_token and (method, p) in fake.addon_denied:
                    return self._out(403, {"detail": "add-on token: route outside its permissions"})
                if p == "/thumbnail" and method == "GET":
                    if q.get("path") not in fake.photos:
                        return self._out(404, {"detail": "Thumbnail not found"})
                    data = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"fakejpeg" + q["path"].encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return None
                if p == "/api/photos/plugin_tags" and method == "POST" and fake.plugin_tags:
                    return self._out(422, {"detail": "photo_paths required"})
                if p == "/api/auth/edition/login" and method == "POST":
                    fake.logins += 1
                    if body.get("password") != fake.password:
                        return self._out(401, {"detail": "Invalid password"})
                    tok = f"tok-{fake.logins + 1}"
                    fake.tokens.add(tok)
                    return self._out(200, {"access_token": tok, "token_type": "bearer"})
                if p == "/api/config":
                    return self._out(200, {"version": fake.version})
                if p == "/api/albums":
                    return self._out(200, {"albums": [{"id": 1, "name": "Night", "photo_count": 2}]})
                if p == "/api/photos" and method == "GET":
                    rows = list(fake.photos.values())
                    if q.get("path_prefix"):
                        pre = q["path_prefix"].replace("\\", "/").rstrip("/") + "/"
                        rows = [r for r in rows if r["path"].replace("\\", "/").startswith(pre)]
                    if q.get("scan_run"):
                        rows = [r for r in rows if str(r.get("scan_run")) == q["scan_run"]]
                    if q.get("tag"):
                        rows = [r for r in rows if q["tag"] in r["manual_tags"]]
                    if q.get("category"):
                        rows = [r for r in rows if r.get("category") == q["category"]]
                    if q.get("date_from"):
                        rows = [r for r in rows if (r.get("date_taken") or "").replace(":", "-", 2) >= q["date_from"]]
                    per, page = int(q.get("per_page", 64)), int(q.get("page", 1))
                    chunk = rows[(page - 1) * per: page * per]
                    return self._out(200, {"photos": [self._view(r) for r in chunk], "total": len(rows),
                                           "page": page, "per_page": per,
                                           "total_pages": -(-len(rows) // per), "has_more": page * per < len(rows)})
                if p == "/api/photo" and method == "GET":
                    r = fake.photos.get(q.get("path"))
                    return self._out(200, self._view(r)) if r else self._out(404, {"detail": "Photo not found"})
                if p == "/api/photo/annotations":
                    if method == "GET" and fake.annotations_read:
                        return self._out(200, {"annotations": [{"source": s, "payload": pl} for (pp, s), pl
                                                               in sorted(fake.annotations.items()) if pp == q.get("path")]})
                    if method == "PUT" and fake.annotations_put:
                        if not self._authed():
                            return self._out(401, {"detail": "auth"})
                        if not body.get("path"):
                            return self._out(422, {"detail": "path required"})
                        fake.annotations[(body["path"], body["source"])] = body["payload"]
                        return self._out(200, {"success": True})
                    if method == "DELETE" and fake.annotations_read:
                        if not self._authed():
                            return self._out(401, {"detail": "auth"})
                        removed = fake.annotations.pop((q.get("path"), q.get("source")), None) is not None
                        return self._out(200, {"success": True, "removed": removed})
                    return self._out(405, {"detail": "Method Not Allowed"})
                if method in ("POST", "PUT", "DELETE") and not self._authed():
                    return self._out(401, {"detail": "Edition login required"})
                if p == "/api/photos/batch_manual_tags":
                    n = 0
                    for path in body["photo_paths"]:
                        r = fake.photos.get(path)
                        if r is None:
                            continue
                        ai = {t.strip() for t in (r.get("tags") or "").split(",") if t.strip()}
                        if body["action"] == "add":
                            if body["tag"] in r["manual_tags"] or body["tag"] in ai or len(r["manual_tags"]) >= CAP:
                                continue
                            r["manual_tags"] = sorted(r["manual_tags"] + [body["tag"]])
                            n += 1
                        elif body["tag"] in r["manual_tags"]:
                            r["manual_tags"].remove(body["tag"])
                            n += 1
                    return self._out(200, {"success": True, "count": n})
                if p == "/api/photo/manual_tags" and method == "DELETE":
                    r = fake.photos.get(body.get("path"))
                    if r is None:
                        return self._out(404, {"detail": "Photo not found"})
                    removed = body["tag"] in r["manual_tags"]
                    if removed:
                        r["manual_tags"].remove(body["tag"])
                    return self._out(200, {"success": True, "tag": body["tag"], "removed": removed})
                if p == "/api/photos/batch_gps" and method == "POST":
                    f = body.get("filters") or {}
                    rows = list(fake.photos.values())
                    if f.get("path_prefix"):
                        pre = f["path_prefix"].replace("\\", "/").rstrip("/") + "/"
                        rows = [r for r in rows if r["path"].replace("\\", "/").startswith(pre)]
                    if f.get("album_id"):
                        rows = [r for r in rows if int(f["album_id"]) in r.get("albums", ())]
                    coords = (body["gps_latitude"], body["gps_longitude"])
                    changed = [r for r in rows if (r.get("gps_latitude"), r.get("gps_longitude")) != coords
                               and (not body.get("only_missing", True) or r.get("gps_latitude") is None)]
                    token = f"tok-{len(rows)}-{len(changed)}-{coords}"
                    if not body.get("preview"):
                        if body.get("preview_token") != token:
                            return self._out(409, {"detail": "Selection changed; preview again before applying"})
                        for r in changed:
                            r.update(gps_latitude=coords[0], gps_longitude=coords[1], gps_manually_set=1)
                    return self._out(200, {"changed": len(changed), "skipped": len(rows) - len(changed),
                                           "preview_token": token})
                if p == "/api/photo/gps" and method == "PUT":
                    r = fake.photos.get(body.get("path"))
                    if r is None:
                        return self._out(404, {"detail": "Photo not found"})
                    r.update(gps_latitude=body["gps_latitude"], gps_longitude=body["gps_longitude"], gps_manually_set=1)
                    return self._out(200, {"gps_latitude": r["gps_latitude"], "gps_longitude": r["gps_longitude"]})
                return self._out(404, {"detail": "Not Found"})

            @staticmethod
            def _view(r):
                return fake.view(r)

            def do_GET(self):
                self.handle_one("GET")

            def do_POST(self):
                self.handle_one("POST")

            def do_PUT(self):
                self.handle_one("PUT")

            def do_DELETE(self):
                self.handle_one("DELETE")

        return H
