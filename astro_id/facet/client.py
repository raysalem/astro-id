"""HTTP client for the Facet viewer API (the only way the sidecar touches a Facet library).

Credentials come from the environment, never from Facet's config or database:

- ``FACET_TOKEN``: a bearer token used as is (for when Facet accepts per-plugin tokens on ``/api``).
- ``FACET_USERNAME`` + ``FACET_PASSWORD``: multi-user installs, ``POST /api/auth/login`` (an admin account has
  edition rights).
- ``FACET_EDITION_PASSWORD``: single-user installs, ``POST /api/auth/edition/login`` (the edition JWT also
  reads, so no viewer password is needed).
- none of them: anonymous; reads work on an open install, every write is refused (Facet is read-only without
  an edition login).

As a Facet add-on (``astro-id facet serve --addon``, started by Facet's supervisor) the credential is the per-launch
add-on token Facet passes in ``FACET_TOKEN``; see :func:`addon_credentials`. An ``fct_`` token talks to Facet over MCP
(:mod:`astro_id.facet.mcp_client`, PLUGIN_BOUNDARY.md v0.3 §6a): :func:`connect` picks the client. This module's
:class:`FacetClient` is the password (``/api``) path of the standalone sidecar.

Routes used (Facet 1.29 names): ``GET /api/config`` (version), ``GET /api/photos`` (paged listing),
``GET /api/photo`` (one photo), ``GET /api/albums``, ``GET /thumbnail``, ``POST /api/photos/batch_manual_tags``,
``DELETE /api/photo/manual_tags``, ``POST /api/photos/batch_gps`` (a folder or album), and, when the server has them,
``GET|PUT|DELETE /api/photo/annotations``.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from urllib.parse import quote

import requests

log = logging.getLogger("astro_id.facet")

DEFAULT_URL = "http://127.0.0.1:5000"
PAGE_SIZE = 500            # GET /api/photos per_page maximum
BATCH_MAX = 1000           # photo_paths per batch write
# The gallery hides most of a burst / duplicate / bracket set by default; a sidecar wants every frame.
ALL_FRAMES = {"hide_blinks": "0", "hide_bursts": "0", "hide_duplicates": "0", "hide_brackets": "0",
              "hide_panoramas": "0", "hide_rejected": "0", "collapse_stacks": "0"}


class FacetError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, *, code: str | None = None, issues=None):
        super().__init__(message)
        self.status = status
        self.code = code          # the MCP envelope's error code, when the call went over MCP
        self.issues = issues


class FacetClient:
    def __init__(self, url: str | None = None, *, token: str | None = None, edition_password: str | None = None,
                 username: str | None = None, password: str | None = None, timeout: float = 60.0,
                 session: requests.Session | None = None):
        self.url = (url or os.environ.get("FACET_URL") or DEFAULT_URL).rstrip("/")
        self.static_token = token
        self.edition_password = edition_password
        self.username = username
        self.password = password
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.trust_env = False      # a local viewer: never route through a system proxy
        self.token: str | None = token
        self._features: dict | None = None
        self.version: str | None = None

    @classmethod
    def from_env(cls, url: str | None = None, **kw) -> "FacetClient":
        e = os.environ
        return cls(url, token=e.get("FACET_TOKEN") or None, edition_password=e.get("FACET_EDITION_PASSWORD") or None,
                   username=e.get("FACET_USERNAME") or None, password=e.get("FACET_PASSWORD") or None, **kw)

    # ---------------------------------------------------------------- auth ---
    @property
    def can_login(self) -> bool:
        return bool(self.static_token or self.edition_password or (self.username and self.password))

    def login(self) -> str | None:
        """Get a session token; None when no credential is configured (anonymous, read-only)."""
        if self.static_token:
            self.token = self.static_token
            return self.token
        if self.username and self.password:
            r = self._raw("POST", "/api/auth/login", json={"username": self.username, "password": self.password},
                          auth=False)
        elif self.edition_password:
            r = self._raw("POST", "/api/auth/edition/login", json={"password": self.edition_password}, auth=False)
        else:
            return None
        if r.status_code != 200:
            raise FacetError(f"Facet login failed: HTTP {r.status_code} {self._detail(r)}", r.status_code)
        self.token = r.json().get("access_token")
        if not self.token:
            raise FacetError("Facet login returned no access_token")
        return self.token

    # ------------------------------------------------------------- transport ---
    def _raw(self, method: str, path: str, *, params=None, json=None, auth: bool = True) -> requests.Response:
        headers = {"Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            return self.http.request(method, self.url + path, params=params, json=json, headers=headers,
                                     timeout=self.timeout)
        except requests.RequestException as exc:
            raise FacetError(f"cannot reach Facet at {self.url}: {exc}") from None

    @staticmethod
    def _detail(r: requests.Response) -> str:
        try:
            d = r.json().get("detail")
            return str(d)[:300] if d else ""
        except ValueError:
            return (r.text or "")[:300]

    def request(self, method: str, path: str, *, params=None, json=None, ok=(200,)) -> requests.Response:
        """One API call; logs in on demand and once more on a 401 (token expired)."""
        if self.token is None and self.can_login:
            self.login()
        r = self._raw(method, path, params=params, json=json)
        if r.status_code == 401 and self.can_login and not self.static_token:
            self.login()
            r = self._raw(method, path, params=params, json=json)
        if r.status_code not in ok:
            raise FacetError(f"{method} {path}: HTTP {r.status_code} {self._detail(r)}", r.status_code)
        return r

    # ------------------------------------------------------------ discovery ---
    def server_version(self) -> str | None:
        if self.version is None:
            try:
                self.version = self.request("GET", "/api/config").json().get("version") or "unknown"
            except FacetError as exc:
                log.warning("Facet version unknown (%s)", exc)
                self.version = "unknown"
        return self.version

    @property
    def scoped(self) -> bool:
        """A Facet add-on token (``fct_...``): Facet answers 403 on every route outside its permissions, so a 403
        means "not usable" rather than "exists but needs a login"."""
        return bool(self.static_token and self.static_token.startswith("fct_"))

    def features(self) -> dict:
        """Which optional routes this Facet has. Probed with requests that cannot write anything:
        a PUT/POST/GET with a missing parameter answers 422 when the route exists, 404/405 when it does not.
        With an add-on token only 422 (or 200) counts: 403 = outside the add-on's permissions."""
        if self._features is not None:
            return self._features
        ok_write = (422,) if self.scoped else (401, 403, 422)
        ok_read = (200, 422) if self.scoped else (200, 401, 403, 422)
        f = {}
        r = self._probe("PUT", "/api/photo/annotations", json={})
        f["annotations_put"] = r in ok_write
        r = self._probe("GET", "/api/photo/annotations")
        f["annotations_read"] = r in ok_read
        r = self._probe("POST", "/api/photos/plugin_tags", json={})
        f["plugin_tags"] = r in ok_write
        # Model D (plugin summary tags, indexed detections, batch overlays) is an add-on token's MCP contract;
        # this password path keeps per-object tags.
        f["model_d"] = False
        self._features = f
        return f

    @contextmanager
    def job(self):
        """Same surface as the MCP client's one-session-per-job block; plain HTTP needs nothing held."""
        yield self

    def _probe(self, method: str, path: str, **kw) -> int:
        if self.token is None and self.can_login:
            self.login()
        try:
            return self._raw(method, path, **kw).status_code
        except FacetError:
            return 0

    # ---------------------------------------------------------------- reads ---
    def list_photos(self, *, path_prefix: str | None = None, category: str | None = None, tag: str | None = None,
                    album_id: int | str | None = None, date_from: str | None = None, limit: int | None = None,
                    extra: dict | None = None) -> list[dict]:
        """Every matching photo (all pages), every frame of every set (the gallery's hide toggles off)."""
        params = dict(ALL_FRAMES)
        for k, v in (("path_prefix", path_prefix), ("category", category), ("tag", tag), ("album_id", album_id),
                     ("date_from", date_from)):
            if v not in (None, ""):
                params[k] = str(v)
        params.update({k: ([str(x) for x in v] if isinstance(v, (list, tuple)) else str(v))
                       for k, v in (extra or {}).items()})
        params.update({"per_page": str(PAGE_SIZE), "sort": "date_taken", "dir": "ASC"})
        out: list[dict] = []
        page = 1
        while True:
            params["page"] = str(page)
            doc = self.request("GET", "/api/photos", params=params).json()
            photos = doc.get("photos") or []
            out.extend(photos)
            if limit and len(out) >= limit:
                return out[:limit]
            if not photos or not doc.get("has_more", page < int(doc.get("total_pages") or 0)):
                return out
            page += 1

    def photo(self, path: str) -> dict | None:
        try:
            return self.request("GET", "/api/photo", params={"path": path}).json()
        except FacetError as exc:
            if exc.status == 404:
                return None
            raise

    def albums(self) -> list[dict]:
        doc = self.request("GET", "/api/albums").json()
        return doc.get("albums", doc) if isinstance(doc, dict) else doc

    def thumbnail(self, path: str, size: int | None = None) -> tuple[bytes, str]:
        """(bytes, content type) of Facet's thumbnail for one photo (``photos.read``)."""
        params = {"path": path}
        if size:
            params["size"] = str(int(size))
        if self.token is None and self.can_login:
            self.login()
        headers = {"Accept": "image/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = self.http.get(self.url + "/thumbnail", params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FacetError(f"cannot reach Facet at {self.url}: {exc}") from None
        if r.status_code != 200:
            raise FacetError(f"GET /thumbnail: HTTP {r.status_code}", r.status_code)
        ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not ctype.startswith("image/"):
            raise FacetError(f"GET /thumbnail: not an image ({ctype})", 502)
        return r.content, ctype

    def annotations(self, path: str) -> list[dict]:
        return self.request("GET", "/api/photo/annotations", params={"path": path}).json().get("annotations") or []

    # --------------------------------------------------------------- writes ---
    def batch_add_tag(self, tag: str, paths: list[str]) -> int:
        n = 0
        for i in range(0, len(paths), BATCH_MAX):
            r = self.request("POST", "/api/photos/batch_manual_tags",
                             json={"photo_paths": paths[i:i + BATCH_MAX], "tag": tag, "action": "add"})
            n += int(r.json().get("count") or 0)
        return n

    def delete_tag(self, path: str, tag: str) -> bool:
        r = self.request("DELETE", "/api/photo/manual_tags", json={"path": path, "tag": tag})
        return bool(r.json().get("removed"))

    def batch_remove_tag(self, tag: str, paths: list[str]) -> dict:
        """Remove one tag from ``paths`` (one call per photo on this path): ``{affected, skipped_foreign}``."""
        return {"affected": sum(1 for p in paths if self.delete_tag(p, tag)), "skipped_foreign": 0}

    def set_location(self, lat: float, lon: float, *, path_prefix: str | None = None, album_id: int | None = None,
                     overwrite: bool = False, dry_run: bool = False) -> dict:
        """One location for a whole folder or album (R-LOC), never one photo: preview, then apply with its token."""
        if (path_prefix is None) == (album_id is None):
            raise ValueError("a location is set for a folder or an album, exactly one")
        filters = {k: v for k, v in ALL_FRAMES.items()}
        filters.update({"path_prefix": path_prefix} if path_prefix is not None else {"album_id": str(int(album_id))})
        body = {"filters": filters, "gps_latitude": lat, "gps_longitude": lon, "only_missing": not overwrite}
        preview = self.request("POST", "/api/photos/batch_gps", json={**body, "preview": True}).json()
        out = {"scope": {"path_prefix": path_prefix} if path_prefix is not None else {"album_id": int(album_id)},
               "latitude": lat, "longitude": lon, "overwrite": bool(overwrite), "dry_run": bool(dry_run),
               "changed": preview.get("changed", 0), "skipped": preview.get("skipped", 0)}
        if dry_run or not out["changed"]:
            return out
        done = self.request("POST", "/api/photos/batch_gps",
                            json={**body, "preview_token": preview.get("preview_token")}).json()
        out.update(changed=done.get("changed", 0), skipped=done.get("skipped", 0))
        return out

    def put_annotation(self, path: str, source: str, payload: dict) -> dict:
        return self.request("PUT", "/api/photo/annotations",
                            json={"path": path, "source": source, "payload": payload}).json()

    def delete_annotation(self, path: str, source: str) -> bool:
        r = self.request("DELETE", "/api/photo/annotations", params={"path": path, "source": source})
        return bool(r.json().get("removed"))

    def photo_page_url(self, path: str) -> str:
        """The photo's page in the Facet web app (its location dialog is there)."""
        return f"{self.url}/photo?path={quote(path, safe='')}"


def connect(url: str | None = None, **kw):
    """The client for these credentials: an ``fct_`` token -> Facet's MCP endpoint (R-MCP); otherwise ``/api``."""
    token = kw.get("token")
    if token and token.startswith("fct_"):
        from .mcp_client import McpFacetClient

        return McpFacetClient((url or os.environ.get("FACET_URL") or DEFAULT_URL), token=token,
                              **{k: v for k, v in kw.items() if k in ("timeout", "session")})
    return FacetClient(url, **kw)


def client_from_env(url: str | None = None, **kw):
    e = os.environ
    return connect(url, token=e.get("FACET_TOKEN") or None, edition_password=e.get("FACET_EDITION_PASSWORD") or None,
                   username=e.get("FACET_USERNAME") or None, password=e.get("FACET_PASSWORD") or None, **kw)


# ------------------------------------------------------- add-on credential ---

ADDON_ENV_FILE = "addon-credentials.env"
_TEMP_KEYS = ("FACET_EDITION_PASSWORD", "FACET_USERNAME", "FACET_PASSWORD")


def addon_credentials(addon_id: str, env: dict | None = None) -> tuple[dict, str | None]:
    """The credential an add-on launch uses, as ``FacetClient`` keyword arguments, plus a warning (or None).

    The real path: ``FACET_TOKEN``, the per-launch add-on token Facet mints at start and revokes at stop.

    TEMPORARY (spec §5 "fork prototype"; delete once the Facet fork mints add-on tokens): without ``FACET_TOKEN``,
    a password from the add-on's own env file (``$ASTRO_ID_ADDON_ENV``, else ``addon-credentials.env`` in
    astro-id's data folder; ``KEY=VALUE`` lines with ``FACET_EDITION_PASSWORD`` or ``FACET_USERNAME`` +
    ``FACET_PASSWORD``), then the same keys from the process environment. Facet's clean env never carries them,
    so in a supervised launch only the hand-placed file can supply one."""
    e = os.environ if env is None else env
    token = (e.get("FACET_TOKEN") or "").strip()
    if token:
        return {"token": token}, None
    vals: dict[str, str] = {}
    path = e.get("ASTRO_ID_ADDON_ENV")
    if not path:
        from .ledger import data_dir

        path = str(data_dir() / ADDON_ENV_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() in _TEMP_KEYS:
                        vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in _TEMP_KEYS:
        if not vals.get(k) and e.get(k):
            vals[k] = e[k]
    kw = {"edition_password": vals.get("FACET_EDITION_PASSWORD") or None,
          "username": vals.get("FACET_USERNAME") or None, "password": vals.get("FACET_PASSWORD") or None}
    if kw["edition_password"] or (kw["username"] and kw["password"]):
        return kw, (f"TEMPORARY: Facet passed no FACET_TOKEN; {addon_id} logs in with a password from its own env "
                    "file until the Facet fork mints add-on tokens")
    return {}, f"Facet passed no FACET_TOKEN: {addon_id} is read-only (every write will be refused)"
