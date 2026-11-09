# astro-id Facet plugin

Plate-solves astro photos Facet scans (or you backfill) and writes the identified objects as Facet tags. Single file: `astro_id.py`, stdlib only. Needs the astro-id engine repo (`uv run --project <engine> astro-id ...`, contract v1, see `CONTRACT.md`).

## Install

1. Copy `facet_plugin/astro_id.py` into `<facet>/plugins/` (a junction cannot link a single file; use a copy, or `New-Item -ItemType SymbolicLink` / `HardLink` to keep it current).
2. Add `plugins/astro_id.py` to `<facet>/.git/info/exclude` so it never ships.
3. Enable it in the scoring config OVERRIDE file (`%LOCALAPPDATA%\Facet\config\scoring_config.json`):

```json
"plugins": {
  "enabled": true,
  "astro_id": {
    "engine": "C:/Users/raysa/astro-id",
    "uv": "C:/Users/raysa/.local/bin/uv.exe",
    "min_confidence": 65,
    "roots": ["//192.168.1.90/Photos and Videos/2026/tds-november/astro"],
    "overlay_dir": null,
    "timeout_s": 600,
    "enabled": true
  }
}
```

`engine` is required. `overlay_dir` is passed as `--overlay-dir` (`{folder}` = the photo's folder); unset = engine default. Optional: `batch_min` (4: that many pending frames in one folder run as one folder job), `settle_s` (3: wait so a scan batch can accumulate), `db_path`.

A photo is treated as astro when its category is `astro`, its tags include one of `astrophotography, night sky, stars, milky way, aurora, nebula`, or its path is under a `roots` entry. Restart the scan/viewer after changing the plugin.

## How it runs

`on_score_complete` returns at once for non-astro photos and otherwise queues the path. One background thread runs one engine job at a time (de-duplicated, never blocks the scan). Any failure is logged to `facet.plugins.astro_id` and never raised into Facet. DB path: `plugins.astro_id.db_path`, else `--db` on the scan command line, else `$DB_PATH`, else `photo_scores_pro.db`.

## Backfill

Run with Facet's python from the Facet checkout (so `db.manual_tags` imports), on photos already in Facet's `photos` table:

```powershell
$env:FACET_CONFIG="$env:LOCALAPPDATA\Facet\config\scoring_config.json"; $env:DB_PATH="$env:LOCALAPPDATA\Facet\data\photo_scores_pro.db"
cd C:\Users\raysa\facet-wintest
& "$env:LOCALAPPDATA\Facet\venv\Scripts\python.exe" plugins\astro_id.py "\192.168.1.90\Photos and Videos\2026\tds-november\astro" --min-confidence 65 --dry-run
```

Drop `--dry-run` to write. `--db`, `--config` override the env. Note: a folder run solves every frame in that folder, and only photos that are in Facet's DB receive tags.

## Tag semantics

- Stored in `photo_manual_tags` as `source='user', created_by='astro-id'` (that table's CHECK only allows `user`/`xmp`; `created_by='astro-id'` is the ownership marker).
- Per photo: `plate-solved` plus one tag per object with confidence >= min-confidence (normalized by `normalize_manual_tag`), or `plate-solve failed` when unsolved.
- Never deleted or overwritten: rows with another `created_by` or source. A tag the photo already has from another source (or as an AI tag) is not duplicated.
- The 50-tag cap is respected: status tag first, then highest confidence first; extras are dropped with a log warning.
- An engine-level failure (timeout, bad JSON, contract != 1) writes nothing.

## Idempotency and `--min-confidence`

Each run replaces that photo's `astro-id` rows in one transaction, so re-running gives the same tags. Raising the threshold (65 -> 80) removes the 65-79 tags on re-run; lowering it adds them back. Photos are not re-solved automatically when the config changes; run the backfill.

## Tests

`C:\Users\raysa\AppData\Local\Facet\venv\Scripts\python.exe -m pytest tests/test_facet_plugin.py` from the repo root (skips when Facet is not importable; set `FACET_DIR` to another checkout).
