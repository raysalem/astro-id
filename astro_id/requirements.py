"""What astro-id needs, in one place, and the checks behind ``astro-id check`` / the run-time gate.

Every minimum below is stated once here; docs/REQUIREMENTS.md repeats them for people. Nothing here imports the
heavy solver stack at import time, so ``astro-id check`` stays fast.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

MIN_PYTHON = (3, 12)                       # pyproject.toml requires-python
# ASTAP's CLI prints "ASTAP astrometric solver version CLI-2026.10.30, ..." (date-based YYYY.MM.DD). 2026.10.30 is
# the release astro-id was built and tested with. The flags it uses (-f -fov -r -d -D -z) are long-standing, but no
# older release has been run here, so the oldest known-good one is the floor.
MIN_ASTAP = (2026, 10, 30)
ASTAP_DOWNLOAD = "https://www.hnsky.org/astap.htm"
# Star databases are detected by a "<name>_0101.*" file in the ASTAP folder (or $ASTAP_DB).
REQUIRED_DB = "w08"                        # wide field (fov >= 20 degrees)
OPTIONAL_DBS = ("d50", "d20", "d05", "v50", "h18", "g05")   # narrow field (< 20 degrees); D50 is the recommended one
MIN_FACET_API = 1                          # `facet_api` in the add-on manifest (install.FACET_API)
INTERNET_URL = "https://www.hnsky.org"     # reachability probe for first-run downloads

OK, FAIL, WARN = "ok", "fail", "warn"
MARK = {OK: "✓", FAIL: "✗", WARN: "!"}


class RequirementError(RuntimeError):
    """One plain sentence for the user (no trace)."""


def fmt_version(v: tuple | None) -> str:
    return ".".join(str(n) for n in v) if v else "unknown"


_VER = re.compile(r"(?<!\d)(\d{4})\.(\d{1,2})\.(\d{1,2})(?!\d)")


def parse_astap_version(text: str) -> tuple[int, int, int] | None:
    """Version from ASTAP's banner: 'ASTAP astrometric solver version CLI-2026.10.30, multi-threaded', 'v2026.11.01',
    '2024.05.01b', 'GUI-2026.11.12'. The first YYYY.MM.DD in the text; None when there is none."""
    m = _VER.search(text or "")
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    return (y, mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else None


@lru_cache(maxsize=8)
def astap_version(exe: str) -> tuple[int, int, int] | None:
    """Ask the executable (``-h`` prints the banner and exits at once)."""
    try:
        r = subprocess.run([exe, "-h"], capture_output=True, text=True, timeout=20, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_astap_version((r.stdout or "") + "\n" + (r.stderr or ""))


@dataclass
class AstapStatus:
    exe: Path | None = None
    db: Path | None = None
    version: tuple | None = None
    databases: set = field(default_factory=set)

    @property
    def short_problem(self) -> str | None:
        """The one sentence that stops a solve, or None. An unreadable version is not a stop."""
        if self.exe is None:
            return f"ASTAP is not installed (get the command-line version at {ASTAP_DOWNLOAD} or set ASTAP_PATH)."
        if self.version is not None and self.version < MIN_ASTAP:
            return (f"ASTAP {fmt_version(self.version)} is older than the minimum {fmt_version(MIN_ASTAP)}; "
                    f"update it from {ASTAP_DOWNLOAD}.")
        return None

    @property
    def problem(self) -> str | None:
        p = self.short_problem
        return f"{p} Run `astro-id check` for details." if p else None


def astap_status() -> AstapStatus:
    from .solve import available_databases, find_astap

    exe, db = find_astap()
    if exe is None:
        return AstapStatus()
    return AstapStatus(exe, db, astap_version(str(exe)), available_databases(db))


def require_astap(fallback_ok: bool = False) -> AstapStatus:
    """Gate for ``solve``: raises :class:`RequirementError` when ASTAP is missing (unless another solver can run:
    ``fallback_ok``) or older than the minimum."""
    st = astap_status()
    if st.problem and not (st.exe is None and fallback_ok):
        raise RequirementError(st.problem)
    return st


# --------------------------------------------------------------------------- Facet
def facet_assessment(status: dict | None, features: dict | None, mcp: bool = True) -> dict:
    """What astro-id will do against this Facet. ``status``: the MCP ``status`` answer (``facet_api``);
    ``features``: the client's ``features()``.

    - annotations + plugin tags + batch (model D) -> names become searchable object tags, sky labels on;
    - tags only -> plain per-object tags (sky labels only if annotations can be written);
    - ``facet_api`` is reported only to an add-on token (``mcp``): missing there means a Facet too old for the
      add-on contract; without a token it is simply unknown (``facet_api_ok`` None)."""
    status, features = status or {}, features or {}
    api = status.get("facet_api")
    known = isinstance(api, int)
    api_ok = (api >= MIN_FACET_API) if known else (False if mcp else None)
    labels = bool(features.get("annotations_put"))
    object_tags = bool(features.get("model_d") and features.get("plugin_tags") and labels)
    if object_tags:
        tags, what = "object tags", "names become searchable object tags and sky labels are written"
    elif features.get("tags") or features.get("plugin_tags") or features.get("annotations_put"):
        tags = "plain tags"
        what = "names are written as plain tags; " + ("sky labels are written" if labels else "no sky labels")
    else:
        tags, what = "none", "this Facet connection cannot write tags or sky labels"
    return {"facet_api": api if known else None, "facet_api_ok": api_ok, "sky_labels": labels, "tags": tags,
            "what": what, "model_d": bool(features.get("model_d"))}


def facet_problems(status: dict | None, features: dict | None, mcp: bool = True) -> list[str]:
    """Calm one-line problems for the panel: a Facet that is too old or cannot write."""
    a = facet_assessment(status, features, mcp)
    if a["facet_api_ok"] is False:
        have = f", this Facet has {a['facet_api']}" if a["facet_api"] is not None else ""
        return [f"This Facet is older than Astro needs (add-on API {MIN_FACET_API} or newer{have}): update Facet "
                "for sky labels and object tags."]
    if a["tags"] == "none":
        return ["This Facet connection cannot write tags: check the add-on's permissions."]
    return []


# --------------------------------------------------------------------------- the checks
def _row(key: str, state: str, text: str, fix: str = "") -> dict:
    return {"key": key, "state": state, "text": text, "fix": fix}


def run_checks(offline: bool = False, facet_url: str | None = None, facet_probe=None) -> list[dict]:
    """One dict per requirement: key, state (ok/fail/warn), text, fix. ``facet_probe(url)`` ->
    (version, status, features, mcp) is injectable for tests."""
    rows: list[dict] = []
    py = sys.version_info[:3]
    py_ok = py[:2] >= MIN_PYTHON
    rows.append(_row("python", OK if py_ok else FAIL,
                     f"Python {'.'.join(map(str, py))} (need {'.'.join(map(str, MIN_PYTHON))} or newer)",
                     "" if py_ok else "install Python 3.12 or newer (uv python install 3.12)"))
    uv = shutil.which("uv")
    rows.append(_row("uv", OK if uv else WARN, f"uv found: {uv}" if uv else "uv not found",
                     "" if uv else "install uv (https://docs.astral.sh/uv/); the Facet add-on is started with it"))
    st = astap_status()
    if st.exe is None:
        rows.append(_row("astap", FAIL, "ASTAP not found",
                         f"install the ASTAP command-line version from {ASTAP_DOWNLOAD}, or set ASTAP_PATH"))
    elif st.version is None:
        rows.append(_row("astap", WARN, f"ASTAP found: {st.exe}; its version could not be read "
                         f"(need {fmt_version(MIN_ASTAP)} or newer)", "run it with -h and check the version"))
    elif st.version < MIN_ASTAP:
        rows.append(_row("astap", FAIL, f"ASTAP {fmt_version(st.version)} at {st.exe} is older than the minimum "
                         f"{fmt_version(MIN_ASTAP)}", f"update from {ASTAP_DOWNLOAD}"))
    else:
        rows.append(_row("astap", OK, f"ASTAP {fmt_version(st.version)} at {st.exe} "
                         f"(minimum {fmt_version(MIN_ASTAP)})"))
    if st.exe is None:
        rows.append(_row("database", FAIL, "star database not checked (no ASTAP)", "install ASTAP first"))
    elif REQUIRED_DB in st.databases:
        narrow = [d.upper() for d in OPTIONAL_DBS if d in st.databases]
        rows.append(_row("database", OK, f"star database {REQUIRED_DB.upper()} found in {st.db}"
                         + (f" (also {', '.join(narrow)})" if narrow else "")))
        if not narrow:
            rows.append(_row("database_narrow", WARN, "no narrow-field database (D50) installed",
                             "optional: install D50 from the ASTAP site for fields under 20 degrees"))
    else:
        have = ", ".join(sorted(d.upper() for d in st.databases)) or "none"
        rows.append(_row("database", FAIL, f"star database {REQUIRED_DB.upper()} missing (found: {have})",
                         f"install W08 into {st.exe.parent} (or point ASTAP_DB at its folder); "
                         f"download it from {ASTAP_DOWNLOAD}"))
    try:
        with tempfile.TemporaryDirectory(prefix="astro-id-check-") as d:
            (Path(d) / "t.txt").write_text("ok")
        rows.append(_row("temp", OK, "temp folder is writable"))
    except OSError as exc:
        rows.append(_row("temp", FAIL, f"cannot write to the temp folder ({exc.strerror or exc})",
                         "free disk space or point TEMP at a writable folder"))
    if not offline:
        rows.append(_internet_row())
    if facet_url:
        rows.extend(_facet_rows(facet_url, facet_probe or probe_facet))
    return rows


def _internet_row() -> dict:
    import urllib.request

    try:
        urllib.request.urlopen(urllib.request.Request(INTERNET_URL, method="HEAD"), timeout=5).close()
        return _row("internet", OK, "internet reachable (first-run catalog downloads)")
    except Exception:
        return _row("internet", WARN, "internet not reachable",
                    "only needed once, for the first-run catalog downloads; use --offline to skip this check")


def probe_facet(url: str):
    """(version, status dict, features dict, mcp bool) from a Facet; raises on failure. Uses FACET_TOKEN etc. from
    the environment: an add-on token reads ``status`` over MCP, anything else the plain HTTP API."""
    from .facet.client import client_from_env

    c = client_from_env(url)
    mcp = getattr(c, "mode", None) == "mcp"
    version = c.server_version()
    if version in (None, "unknown"):    # the clients swallow connection errors into "unknown"
        raise ConnectionError("no answer from its version/status route")
    return version, (c.status() if mcp else {}), c.features(), mcp


def _facet_rows(url: str, probe) -> list[dict]:
    try:
        version, status, feats, mcp = probe(url)
    except Exception as exc:
        why = (str(exc).splitlines() or [type(exc).__name__])[0][:140]
        return [_row("facet", FAIL, f"Facet at {url} is not reachable ({why})",
                     "start Facet, check the URL, and set FACET_TOKEN to an add-on token")]
    rows = [_row("facet", OK, f"Facet reachable at {url}, version {version or 'unknown'}")]
    a = facet_assessment(status, feats, mcp)
    if a["facet_api_ok"]:
        rows.append(_row("facet_api", OK, f"Facet add-on API {a['facet_api']} (minimum {MIN_FACET_API})"))
    elif a["facet_api_ok"] is None:
        rows.append(_row("facet_api", WARN, f"Facet add-on API not reported (minimum {MIN_FACET_API})",
                         "set FACET_TOKEN to an add-on token so Facet reports it"))
    else:
        rows.append(_row("facet_api", FAIL, f"Facet add-on API not reported (minimum {MIN_FACET_API}): this "
                         "Facet is too old for the add-on", "update Facet"))
    full = a["model_d"] and a["sky_labels"] and a["tags"] == "object tags"
    rows.append(_row("facet_mode", OK if full else WARN, f"astro-id will use: {a['tags']}; {a['what']}",
                     "" if full else "update Facet for searchable object tags and sky labels"))
    return rows


def summarize(rows: list[dict]) -> dict:
    return {"ok": not any(r["state"] == FAIL for r in rows), "checks": rows,
            "minimums": {"python": ".".join(map(str, MIN_PYTHON)), "astap": fmt_version(MIN_ASTAP),
                         "database": REQUIRED_DB.upper(), "facet_api": MIN_FACET_API}}


def render(rows: list[dict]) -> str:
    out = []
    for r in rows:
        out.append(f"{MARK[r['state']]} {r['text']}")
        if r["fix"] and r["state"] != OK:
            out.append(f"    -> {r['fix']}")
    return "\n".join(out)


def requirements_block(client=None) -> dict:
    """The ``requirements`` block of api/state: {ok, problems: [one calm sentence each]}. No new Facet calls beyond
    what the add-on already made (status/features are cached by the client)."""
    problems: list[str] = []
    st = astap_status()
    if st.short_problem:
        problems.append(st.short_problem)
    elif st.exe is not None and REQUIRED_DB not in st.databases:
        problems.append(f"ASTAP star database {REQUIRED_DB.upper()} is missing; wide-field photos will not solve.")
    if client is not None:
        try:
            mcp = getattr(client, "mode", None) == "mcp"
            problems += facet_problems(client.status() if mcp else {}, client.features(), mcp)
        except Exception:
            pass
    return {"ok": not problems, "problems": problems}
