"""P3: drive the RUNNING astro-id add-on (started by Facet's supervisor) over its own HTTP API, like the panel.

Reads the add-on port from <data>/logs/addons/astro-id.log (last FACET_ADDON_PORT line) and the launch secret
from <data>/p3-launch.json (X-Facet-Launch). Not a pytest module.

    uv run --project <astro-id-p3> python tests/live/p3_addon_http.py <data> state|annotations|solve|save|export [photo ...]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

PFX = os.environ["ASTRO_ID_P3_PREFIX"]   # the photo folder as Facet stores it, with a trailing separator


def main() -> int:
    data, cmd, photos = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    log = (data / "logs" / "addons" / "astro-id.log").read_text(encoding="utf-8", errors="replace")
    port = re.findall(r"FACET_ADDON_PORT=(\d+)", log)[-1]
    secret = json.loads((data / "p3-launch.json").read_text(encoding="utf-8"))["FACET_LAUNCH_SECRET"]
    base = f"http://127.0.0.1:{port}/ui/api/"
    s = requests.Session()
    s.trust_env = False
    s.headers["X-Facet-Launch"] = secret
    paths = [PFX + p for p in photos]

    def get(rel, **params):
        r = s.get(base + rel, params=params, timeout=120)
        return r.status_code, r.json()

    def post(rel, body):
        r = s.post(base + rel, json=body, timeout=600)
        return r.status_code, r.json()

    out: dict = {}
    if cmd == "state":
        out = get("state")[1]
    elif cmd == "annotations":
        out = {p.rsplit("\\", 1)[-1]: get("annotations", path=p) for p in paths}
    elif cmd == "solve":
        t0 = time.time()
        out["start"] = post("solve", {"paths": paths, "offset": "+2559d11h08m", "tolerance_min": 15,
                                      "tz": "America/Los_Angeles", "source": "jpg", "min_confidence": 65,
                                      "site": "32.614028,-116.333056,1128.8"})
        while True:
            st, job = get("job", after=0)
            if not job.get("running"):
                break
            time.sleep(2)
        out["seconds"] = round(time.time() - t0)
        out["error"] = job.get("error")
        done = job.get("done") or {}
        out["solved_by_path"] = done.get("solved_by_path")
        out["review"] = done.get("review")
    elif cmd == "save":
        st, d = post("save", {"min_confidence": 65})
        out = {"status": st, **{k: v for k, v in d.items() if k != "reports"},
               "reports": [{k: r.get(k) for k in ("path", "add", "remove", "migrated", "legacy", "annotation",
                                                  "searchable", "error")} for r in d.get("reports") or []]}
    elif cmd == "export":
        out["start"] = post("export", {"paths": paths, "view": {}, "options": {"lines": True, "scores": False,
                                                                              "legend": True}})
        while True:
            st, ej = get("export")
            if not ej.get("running"):
                break
            time.sleep(1)
        out["export"] = ej
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
