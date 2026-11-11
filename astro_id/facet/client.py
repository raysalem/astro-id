"""HTTP client for the Facet viewer API (the only way the sidecar touches a Facet library).

Credentials come from the environment, never from Facet's config or database:

- ``FACET_TOKEN``: a bearer token used as is (for when Facet accepts per-plugin tokens on ``/api``).
- ``FACET_USERNAME`` + ``FACET_PASSWORD``: multi-user installs, ``POST /api/auth/login`` (an admin account has
  edition rights).
- ``FACET_EDITION_PASSWORD``: single-user installs, ``POST /api/auth/edition/login`` (the edition JWT also
  reads, so no viewer password is needed).
- none of them: anonymous; reads work on an open install, every write is refused (Facet is read-only without
  an edition login).

Routes used (Facet 1.29 names): ``GET /api/config`` (version), ``GET /api/photos`` (paged listing),
``GET /api/photo`` (one photo), ``GET /api/albums``, ``POST /api/photos/batch_manual_tags``,
``DELETE /api/photo/manual_tags``, ``PUT /api/photo/gps``, and, when the server has them,
``GET|PUT|DELETE /api/photo/annotations``.
"""
from __future__ import annotations

import logging
import os
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
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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

    def features(self) -> dict:
        """Which optional routes this Facet has. Probed with requests that cannot write anything:
        a PUT/GET with a missing parameter answers 422 when the route exists, 404/405 when it does not."""
        if self._features is not None:
            return self._features
        f = {}
        r = self._probe("PUT", "/api/photo/annotations", json={})
        f["annotations_put"] = r in (401, 403, 422)
        r = self._probe("GET", "/api/photo/annotations")
        f["annotations_read"] = r in (200, 401, 403, 422)
        self._features = f
        return f

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
        params.update({k: str(v) for k, v in (extra or {}).items()})
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

    def put_gps(self, path: str, lat: float | None, lon: float | None) -> dict:
        return self.request("PUT", "/api/photo/gps",
                            json={"path": path, "gps_latitude": lat, "gps_longitude": lon}).json()

    def put_annotation(self, path: str, source: str, payload: dict) -> dict:
        return self.request("PUT", "/api/photo/annotations",
                            json={"path": path, "source": source, "payload": payload}).json()

    def delete_annotation(self, path: str, source: str) -> bool:
        r = self.request("DELETE", "/api/photo/annotations", params={"path": path, "source": source})
        return bool(r.json().get("removed"))

    def photo_page_url(self, path: str) -> str:
        """The photo's page in the Facet web app (its location dialog is there)."""
        return f"{self.url}/photo?path={quote(path, safe='')}"
