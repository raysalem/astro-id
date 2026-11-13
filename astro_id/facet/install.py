"""``astro-id facet install | uninstall``: place (or remove) astro-id's Facet add-on manifest.

Spec: PLUGIN_BOUNDARY.md v0.2 §4. ``install`` writes ``<facet data>/addons/astro-id/addon.json`` and nothing else
(Facet never writes add-on config; it reads the manifest at start-up and on reload). ``uninstall`` deletes that
file and the then-empty ``astro-id`` folder. Neither touches Facet's ``scoring_config.json``: the operator switch
``addons.enabled`` stays the operator's.

The manifest's ``start.argv`` is absolute: ``argv[0]`` is uv, resolved here (``$UV``, else ``uv`` on PATH, else
``~/.local/bin/uv``) because Facet does no PATH lookup; ``--project`` is this checkout.

``network`` (model D Facet): the hosts astro-id's backend contacts, shown on Facet's Add-ons page. An older Facet
(local/addons-annot) refuses a manifest with any key it does not know, so ``--older-facet`` leaves it out.
No ``features`` are declared: astro-id reads what Facet speaks from ``status`` and adapts, so naming one would only
make an older Facet refuse it. The id stays ``astro-id`` (renaming orphans the data already written under it).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ADDON_ID = "astro-id"
MANIFEST = "addon.json"
FACET_API = 1
PERMISSIONS = ["photos.read", "tags.write", "annotations.write", "gps.write"]
SLOTS = ["sidebar", "import_hook"]
# Every host the add-on's backend calls (grep "https://" in astro_id/): USGS elevation for a site's height, the
# nova.astrometry.net fallback solver (only with ASTROMETRY_API_KEY), and the catalog downloads on first use.
NETWORK = ["epqs.nationalmap.gov", "nova.astrometry.net", "cdsarc.cds.unistra.fr", "vizier.cfa.harvard.edu",
           "raw.githubusercontent.com", "www.pas.rochester.edu"]


def project_dir() -> Path:
    """This astro-id checkout (the folder holding pyproject.toml)."""
    root = Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file():
        raise ValueError(f"cannot find astro-id's pyproject.toml above {Path(__file__).resolve().parent}; "
                         "pass --project")
    return root


def resolve_uv(explicit: str | None = None, env: dict | None = None) -> Path:
    e = os.environ if env is None else env
    cands = [explicit] if explicit else []
    if e.get("UV"):
        cands.append(e["UV"])
    if not cands:
        w = shutil.which("uv")
        if w:
            cands.append(w)
        home = Path.home() / ".local" / "bin"
        cands += [str(home / "uv.exe"), str(home / "uv")]
    for c in cands:
        p = Path(c).expanduser()
        if p.is_file():
            return p.resolve()
    raise ValueError("uv not found: set UV to its full path (or pass --uv)")


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("astro-id")
    except Exception:   # pragma: no cover - running from an unpackaged tree
        return "0.0.0"


def manifest(uv: Path, project: Path, older_facet: bool = False) -> dict:
    doc = {
        "id": ADDON_ID, "name": "Astro", "version": package_version(), "icon": "star", "facet_api": FACET_API,
        "start": {"argv": [str(uv), "run", "--project", str(project), "astro-id", "facet", "serve", "--addon"],
                  "port": "auto", "health": "/health"},
        "ui": {"path": "/ui/"},
        "slots": list(SLOTS),
        "permissions": list(PERMISSIONS),
    }
    if not older_facet:
        doc["network"] = list(NETWORK)
    return doc


def _data_dir(facet_data: str) -> Path:
    d = Path(facet_data).expanduser()
    if not d.is_dir():
        raise ValueError(f"Facet data folder not found: {d}")
    return d.resolve()


def install(facet_data: str, uv: str | None = None, project: str | None = None,
            older_facet: bool = False) -> tuple[Path, dict]:
    data = _data_dir(facet_data)
    proj = Path(project).expanduser().resolve() if project else project_dir()
    if not (proj / "pyproject.toml").is_file():
        raise ValueError(f"not an astro-id checkout (no pyproject.toml): {proj}")
    doc = manifest(resolve_uv(uv), proj, older_facet)
    folder = data / "addons" / ADDON_ID
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MANIFEST
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target, doc


def uninstall(facet_data: str) -> tuple[Path, bool]:
    """(manifest path, whether it existed). The ``astro-id`` folder goes too when nothing else is in it."""
    folder = _data_dir(facet_data) / "addons" / ADDON_ID
    target = folder / MANIFEST
    existed = target.is_file()
    if existed:
        target.unlink()
    try:
        folder.rmdir()
    except OSError:
        pass   # absent, or holds something we did not put there: leave it
    return target, existed


def run(args) -> int:
    if args.facet_cmd == "install":
        target, doc = install(args.facet_data, getattr(args, "uv", None), getattr(args, "project", None),
                              bool(getattr(args, "older_facet", False)))
        print(f"wrote {target}")
        print(json.dumps(doc, indent=2))
        print("Facet lists the add-on after a restart or a config reload, once addons.enabled is true in its "
              "scoring_config.json.", file=sys.stderr)
        return 0
    target, existed = uninstall(args.facet_data)
    print(f"removed {target}" if existed else f"nothing to remove: {target}")
    return 0
