"""P3 (test audit 2026-11-15): astro-id model D against a REAL Facet fix/review-all on a COPY of real data.

Not a pytest module (nothing here runs in ``uv run pytest``). It drives the same writer the panel's
"Save to Facet" uses (``sync.apply_results`` with an add-on ``SyncContext``) over Facet's /mcp with the add-on
token Facet's supervisor handed the add-on (``p3-launch.json``, written by the launch wrapper), and reads the
copy database READ-ONLY to verify. Results are rebuilt from the sky-label payloads the copy already stores
(no solve): object = payload item (name = label, confidence = score, group -> level; stars and cluster members are
not taggable), solved for photos with a payload, failed for the others.

    uv run --project <astro-id-p3> python tests/live/p3_model_d.py <phase> --data <astro-p3 folder> [--cut N]

Phases: db (row counts), probe (features / fallback), guard (Facet's remove_tags ownership on a user row),
dry (plan only, nothing written), save (one real save; counts every write call), check (search / detections /
summary). Each prints JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from astro_id.facet import sync as S
from astro_id.facet.ledger import Ledger
from astro_id.facet.mcp_client import McpFacetClient

PFX = os.environ["ASTRO_ID_P3_PREFIX"]   # the photo folder as Facet stores it, with a trailing separator
LEVEL = {"constellation": 1, "planet": 2, "catalog": 3, "asterism": 4, "star": 5}
WRITES = ("batch_add_tag", "batch_remove_tag", "delete_tag", "put_annotation", "put_annotations_batch",
          "delete_annotation", "set_location")


def ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def db_counts(db: Path) -> dict:
    c = ro(db)
    out = {"manual_tags_by_source_created_by": [list(r) for r in c.execute(
        "SELECT source, COALESCE(created_by, 'NULL'), COUNT(*), COUNT(DISTINCT photo_path) FROM photo_manual_tags "
        "GROUP BY 1, 2 ORDER BY 1, 2")]}
    out["astro_folder_tags"] = [list(r) for r in c.execute(
        "SELECT source, COALESCE(created_by, 'NULL'), COUNT(*) FROM photo_manual_tags WHERE photo_path LIKE ? "
        "GROUP BY 1, 2 ORDER BY 1, 2", (PFX + "%",))]
    out["plugin_per_photo_max"] = c.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM photo_manual_tags WHERE source = 'plugin' GROUP BY photo_path, "
        "created_by)").fetchone()[0]
    has = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "photo_detections" in has:
        out["detections"] = [list(r) for r in c.execute(
            "SELECT source, COUNT(*), COUNT(DISTINCT photo_path) FROM photo_detections GROUP BY 1")]
    out["annotations"] = [list(r) for r in c.execute(
        "SELECT source, COUNT(*) FROM photo_annotations GROUP BY 1")]
    out["fixtures"] = [list(r) for r in c.execute(
        "SELECT substr(photo_path, length(?) + 1), tag, source, COALESCE(created_by, 'NULL') FROM photo_manual_tags "
        "WHERE photo_path LIKE ? AND (created_by IS NULL OR tag IN ('perseus', 'taurus', 'aries', 'polaris')) "
        "ORDER BY 1, 2", (PFX, PFX + "%"))]
    return out


def rebuilt_results(db: Path) -> dict[str, dict]:
    """{facet path: frame result} from the copy's stored astro-id payloads (+ failed frames for the others)."""
    c = ro(db)
    paths = [r[0] for r in c.execute("SELECT path FROM photos WHERE path LIKE ? ORDER BY path", (PFX + "%",))]
    payloads = {p: json.loads(t) for p, t in c.execute(
        "SELECT photo_path, payload FROM photo_annotations WHERE source = 'astro-id' AND photo_path LIKE ?",
        (PFX + "%",))}
    out = {}
    for p in paths:
        pl = payloads.get(p)
        if pl is None:
            out[p] = {"solved": False, "objects": []}
            continue
        # Drop any index flag a previous model D save left in the stored copy: the writer sets it from the cut.
        items = [{k: v for k, v in it.items() if k != "index"} for it in pl.get("items") or []]
        objs = [{"name": it["label"], "tag": it["label"], "confidence": it.get("score", 0),
                 "level": LEVEL.get(it.get("group"), 9),
                 "taggable": it.get("group") != "star" and not it.get("member_of")} for it in items]
        ann = {k: v for k, v in pl.items() if k != "quality"}
        ann["items"] = items
        out[p] = {"solved": True, "objects": objs, "annotations": ann, "quality": pl.get("quality")}
    return out


def client_for(data: Path) -> McpFacetClient:
    launch = json.loads((data / "p3-launch.json").read_text(encoding="utf-8"))
    return McpFacetClient(launch["FACET_URL"], token=launch["FACET_TOKEN"])


def count_writes(client) -> dict:
    calls: dict[str, int] = {}
    for name in WRITES:
        fn = getattr(client, name)

        def wrapped(*a, _fn=fn, _name=name, **kw):
            calls[_name] = calls.get(_name, 0) + 1
            return _fn(*a, **kw)
        setattr(client, name, wrapped)
    return calls


def summary(reports: list[dict], model_d: bool) -> dict:
    """The same totals ``App.save`` returns to the panel."""
    ok = [r for r in reports if not r.get("error")]
    return {"photos": len(ok), "errors": len(reports) - len(ok),
            "tags_added": sum(len(r.get("add") or []) for r in ok),
            "tags_removed": sum(len(r.get("remove") or []) for r in ok),
            "sky_labels": sum(1 for r in ok if r.get("annotation") in ("stored", "unchanged")),
            "names_as": "searchable" if model_d else "tags",
            "searchable": sum(r.get("searchable", len(r.get("names") or [])
                                    if r.get("annotation") in ("stored", "unchanged") else 0) for r in ok),
            "old_tags_removed": sum(len(r.get("migrated") or []) for r in ok),
            "old_tags_yours": sum(len(r.get("legacy") or []) for r in ok)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["db", "probe", "guard", "dry", "save", "check"])
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--cut", type=int, default=65)
    ap.add_argument("--name", action="append", default=[], help="check: names to search (detected=)")
    a = ap.parse_args()
    db = a.data / "photo_scores_p3.db"
    if a.phase == "db":
        print(json.dumps(db_counts(db), indent=1))
        return 0
    client = client_for(a.data)
    if a.phase == "guard":
        path = PFX + "DSC07610.JPG"
        before = client.photo(path)
        r = client.batch_remove_tag("perseus", [path])
        after = client.photo(path)
        print(json.dumps({"remove_tags perseus on DSC07610 (user's row)": r,
                          "perseus before": "perseus" in (before.get("manual_tags") or []),
                          "perseus after": "perseus" in (after.get("manual_tags") or [])}, indent=1))
        return 0
    with Ledger(a.data / "ledger.sqlite") as ledger:
        ctx = S.SyncContext(client=client, ledger=ledger, addon=True, min_confidence=a.cut).prepare()
        if a.phase == "probe":
            print(json.dumps({"version": ctx.version, "mode": ctx.mode, "model_d": ctx.model_d,
                              "annotations_batch": ctx.annotations_batch, "tags_via": ctx.tags_via,
                              "uncertain": ctx.uncertain, "warnings": ctx.warnings,
                              "features": client.features(), "status": client.status()}, indent=1, default=str))
            return 0
        if a.phase == "check":
            out = {}
            for n in a.name:
                rows = client.list_photos(extra={"detected": n}, category=None)
                out[f"search detected={n}"] = sorted(r["path"].rsplit("\\", 1)[-1] for r in rows)
            p = PFX + "DSC07616.JPG"
            out["get_detections DSC07616"] = client.call("get_detections", {"path": p})
            out["summary DSC07616"] = client.annotations_summary(p)
            ph = client.photo(p)
            out["get_photo DSC07616 tags"] = {k: ph.get(k) for k in ("manual_tags", "plugin_tags", "detections")}
            print(json.dumps(out, indent=1, default=str))
            return 0
        ctx.dry_run = a.phase == "dry"
        results = rebuilt_results(db)
        calls = count_writes(client)
        reports = S.apply_results(ctx, results)
        short = [{k: v for k, v in r.items() if k not in ("names",)} | {"path": r["path"].rsplit("\\", 1)[-1],
                                                                          "names": len(r.get("names") or [])}
                 for r in reports]
        print(json.dumps({"model_d": ctx.model_d, "cut": a.cut, "dry_run": ctx.dry_run, "write_calls": calls,
                          "summary": summary(reports, ctx.model_d), "reports": short}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
