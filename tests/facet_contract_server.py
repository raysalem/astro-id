"""Start a REAL Facet viewer API (``api.create_app``) on a temporary library, for the sidecar contract tests.

Run with Facet's own Python (its venv), never astro-id's::

    <facet venv python> tests/facet_contract_server.py --facet-dir <checkout> --workdir <tmp> --port N

The library is a fresh database built by ``db.schema.init_database`` and seeded with a few photo rows; the
config sets an edition password. Prints ``READY`` on stdout once the app is built, then serves until killed.
Nothing outside ``--workdir`` is written.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

PASSWORD = "contract-edition"
PREFIX = "C:/astro-contract/night"
PHOTOS = [
    {"path": f"{PREFIX}/N1.JPG", "filename": "N1.JPG", "category": "astro", "date_taken": "2019:11:05 09:31:59",
     "gps_latitude": 32.5, "gps_longitude": -116.25, "tags": "night sky,stars", "aggregate": 6.0},
    {"path": f"{PREFIX}/N2.JPG", "filename": "N2.JPG", "category": "astro", "date_taken": "2019:11:05 09:33:10",
     "date_manually_set": 1, "aggregate": 5.5},
    {"path": f"{PREFIX}/N3.JPG", "filename": "N3.JPG", "category": "astro", "date_taken": "2019:11:05 09:34:20",
     "aggregate": 5.0},
    {"path": "C:/astro-contract/day/D1.JPG", "filename": "D1.JPG", "category": "landscape",
     "date_taken": "2019:11:06 12:00:00", "aggregate": 7.0},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facet-dir", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args()
    db = os.path.join(a.workdir, "library.db")
    cfg = os.path.join(a.workdir, "scoring_config.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"viewer": {"password": "", "edition_password": PASSWORD}}, fh)
    os.environ["DB_PATH"] = db
    os.environ["FACET_CONFIG"] = cfg
    os.chdir(a.facet_dir)
    sys.path.insert(0, a.facet_dir)
    from db.schema import init_database

    init_database(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
        for row in PHOTOS:
            row = {k: v for k, v in row.items() if k in cols}
            conn.execute(f"INSERT INTO photos ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
        if "raw_companion" in cols:
            conn.execute("UPDATE photos SET raw_companion = 'N1.ARW' WHERE path = ?", (PHOTOS[0]["path"],))
    import uvicorn
    from api import create_app

    app = create_app()
    print("READY", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
