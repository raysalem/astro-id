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

All keys and defaults (`null` = not passed; the engine then uses the folder's `astro-id.json`, else its own default shown in brackets):

| Key | Default | Meaning |
|---|---|---|
| `engine` | none (required) | astro-id repo folder |
| `uv` | `C:/Users/raysa/.local/bin/uv.exe` | uv executable |
| `enabled` | `true` | plugin on/off |
| `min_confidence` | `65` | tag cut |
| `roots` | `[]` | folders whose photos are always astro |
| `overlay_dir` | `null` [`<folder>-annotated`] | overlays + CSV |
| `timeout_s` | `600` | engine timeout per job |
| `settle_s` | `3` | in-scan worker wait |
| `batch_min` | `4` | pending frames in one folder -> one folder run |
| `db_path` | `null` | else `--db` in argv, `$DB_PATH`, `photo_scores_pro.db` |
| `time_tolerance_min`, `timezone` | `null` | passed as `--time-tolerance`, `--timezone` |
| `tag_levels` | `null` [`[1,2,3,4]`] | levels that become tags: 1 constellation, 2 Sun/Moon/planet, 3 Messier/NGC/IC, 4 asterism, 5 star |
| `star_mag_limit` | `null` [stars off] | e.g. `1.5`: the ~20 brightest named stars become tags |
| `max_catalog_mag` | `null` [`6.0`] | faintest Messier/NGC/IC object to tag |
| `tag_style` | `null` [`plain`] | `plain` (`orion`) or `kind` (`constellation/orion`, `planet/jupiter`) |
| `stack_mode` | `null` [`representative`] | `representative`: solve the middle frame of a burst, reuse it for frames that point the same way; `all`: solve every frame |
| `stack_gap_s` | `null` [`120`] | max seconds between frames of one sequence |
| `catch_up` | `true` | spawn the detached catch-up when a scan exits |
| `log_path` | `null` [`%LOCALAPPDATA%/Facet/data/logs/astro-id.log`] | catch-up log; the lock file `astro-id-catchup.lock` sits next to it |
| `solve_source` | `null` [`jpg`] | pixels the solver reads: `jpg`, `raw` (the same-stem RAW cropped to the JPG frame) or `auto` (RAW when a twin exists). Coordinates and annotations stay in JPG pixel space |
| `tag_targets` | `["facet"]` | `facet` = manual tags on the Facet row; add `raw_xmp` to also write the tags as `dc:subject` keywords into the paired RAW's XMP sidecar (see below). A string like `"facet,raw_xmp"` also works |
| `exiftool` | `null` [`%LOCALAPPDATA%/Facet/exiftool/exiftool.exe`] | exiftool for `raw_xmp`; version >= 13.28 required |
| `overlay_scores` | `false` | passed as `--overlay-scores`: the engine overlay JPG prints `name score` |

Precedence for every option that also exists in the engine: plugin key (passed as the flag) > the folder's `astro-id.json` > engine default. The full engine/`astro-id.json`/plugin table is in the repo `README.md` ("All options").

**Validation.** The config is checked each time it is read: an unknown key logs a warning (and is ignored); a bad value (wrong type or range, unknown `solve_source`/`tag_style`/`stack_mode`/`tag_targets` value) logs an error and the plugin disables itself until fixed, instead of misbehaving.

A photo is treated as astro when its category is `astro`, its tags include one of `astrophotography, night sky, stars, milky way, aurora, nebula`, or its path is under a `roots` entry. Restart the scan/viewer after changing the plugin.

## How it runs

`on_score_complete` returns at once for non-astro photos and otherwise queues the path. One background thread runs one engine job at a time (de-duplicated, never blocks the scan). Any failure is logged to `facet.plugins.astro_id` and never raised into Facet. DB path: `plugins.astro_id.db_path`, else `--db` on the scan command line, else `$DB_PATH`, else `photo_scores_pro.db`.

**Multi-pass scans (the 8gb profile) never emit `on_score_complete`** (`save_photos_partial` has no event). So when the module loads inside a `facet.py` process (`sys.argv[0]` is `facet.py`; the viewer never registers it) it registers an exit hook. At scan exit it checks for pending photos and, if any, spawns a detached catch-up: Facet's python running `plugins/astro_id.py --pending --since <scan start> --db <db>` with `DB_PATH`/`FACET_CONFIG` in its environment, `CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, output appended to `%LOCALAPPDATA%\Facet\data\logs\astro-id.log`. The scan exits at once. A lock file next to the log (pid inside; a dead pid's lock is taken over) keeps catch-ups from overlapping; a running catch-up re-checks for new pending photos up to 3 rounds before it exits.

Pending = no `created_by='astro-id'` rows yet, or `scanned_at` newer than those rows. Candidates: every photo under `roots`, plus astro photos (category/tags) scanned at or after `--since` (the scan's start), so a scan never triggers a sweep of the whole historical library. `--pending` without `--since` considers every astro photo.

Culling flags (blur, burst, best-of-burst) are ignored: soft frames still solve and get tags, and bursts are handled by the engine's stacking sequences (`stack_mode`).

## Backfill

Run with Facet's python from the Facet checkout (so `db.manual_tags` imports), on photos already in Facet's `photos` table:

```powershell
$env:FACET_CONFIG="$env:LOCALAPPDATA\Facet\config\scoring_config.json"; $env:DB_PATH="$env:LOCALAPPDATA\Facet\data\photo_scores_pro.db"
cd C:\Users\raysa\facet-wintest
& "$env:LOCALAPPDATA\Facet\venv\Scripts\python.exe" plugins\astro_id.py "\192.168.1.90\Photos and Videos\2026\tds-november\astro" --min-confidence 65 --dry-run
```

Drop `--dry-run` to write. `--db`, `--config` override the env. `--pending [--since "YYYY-MM-DD HH:MM:SS"]` (UTC) processes the pending set instead of a target folder, under the catch-up lock (what the exit hook runs). Note: a folder run solves every frame in that folder, and only photos that are in Facet's DB receive tags.

## Location and time: Facet is the source of truth

Per photo the plugin reads `gps_latitude/gps_longitude` and `date_taken` + `date_manually_set` from Facet's DB and passes them to the engine:

| Field | Order of precedence |
|---|---|
| Site | Facet GPS (set in Set location, or EXIF) > `astro-id.json` site > none (engine runs without a site, time-dependent IDs are capped) |
| Time | Facet manual date (`date_manually_set=1`, the capture-date shift) > `astro-id.json` `clock_offset` applied to EXIF > raw EXIF (capped) |

`--site lat,lon` is passed without elevation (the engine looks it up on USGS). A non-manual `date_taken` is the camera's wrong clock and is never passed. `date_taken` is a naive camera-local walltime (`YYYY:MM:DD HH:MM:SS`, no timezone); it is sent as a naive `--time`, which the engine reads in the folder's timezone (`astro-id.json`, else inferred from the site), or the plugin's `timezone` key if set. Folder batching only groups photos in the same folder with the same Facet site and no manual date; everything else runs per file. Optional plugin keys: `time_tolerance_min` (passed as `--time-tolerance`), `timezone`.

## Tag semantics

- Stored in `photo_manual_tags` as `source='user', created_by='astro-id'` (that table's CHECK only allows `user`/`xmp`; `created_by='astro-id'` is the ownership marker).
- Per photo: `plate-solved` plus one tag per object the engine marked `taggable` with confidence >= min-confidence (normalized by `normalize_manual_tag`), or `plate-solve failed` when unsolved. The engine decides `taggable` from the tag policy (levels, magnitudes, members suppressed under their cluster/asterism); the plugin only filters.
- Never deleted or overwritten: rows with another `created_by` or source. A tag the photo already has from another source (or as an AI tag) is not duplicated.
- The 50-tag cap is respected: status tag first, then level 1, 2, 3, 4, 5 (constellations, Sun/Moon/planets, catalog objects, asterisms, stars), highest confidence first within a level; extras are dropped with a log warning.
- An engine-level failure (timeout, bad JSON, contract != 1) writes nothing.

## Annotations (Facet viewer overlay)

After the tags, the plugin stores the engine's `annotations` payload (spec: `ANNOTATIONS.md`) in Facet's `photo_annotations` side table (source `astro-id`), in the same transaction as the tag swap. It uses Facet's helper `db.annotations.upsert_annotation(conn, photo_path, source, payload)` (payload is the dict) and never creates the table. If the helper module or the table does not exist yet, it logs once and skips; tags are unaffected. A re-run replaces the row; `plate-solve failed` deletes it; a frame from an older engine without a payload leaves an existing row alone.

## RAW XMP keywords (`tag_targets` including `raw_xmp`)

Off by default because it writes next to your originals. When on, each run also merges the taggable names (the same list as the Facet tags, without `plate-solved`) into the XMP sidecar of the paired RAW (or the photo itself when it is a RAW), named like Facet does: `<image><ext>.xmp` (`DSC07625.ARW.xmp`), as `dc:subject` keywords.

- exiftool is taken from `exiftool` (default `%LOCALAPPDATA%\Facet\exiftool\exiftool.exe`); a version below 13.28 or a missing exiftool is an error and nothing is written.
- Merge, never remove: keywords already in the sidecar that this plugin did not write stay. A wanted keyword the sidecar already has (the user's own) is left as theirs and not claimed.
- Tracking: the names the plugin wrote are recorded in a private XMP namespace bag (`astroid:Keywords`, namespace `https://github.com/astro-id/ns/1.0/`) in the same sidecar. A re-run replaces exactly that bag's keywords; an unsolved frame removes them. (A hierarchical `astro-id|name` prefix was rejected: it would show up as odd literal keywords in other apps.)
- A missing sidecar is created as a minimal XMP packet; `-overwrite_original` is used so no `_original` copies appear.
- Dry run first: `plugins\astro_id.py <folder> --dry-run` prints `xmp <sidecar>: +[added] -[removed] (keeps N other keyword(s))` per photo and writes nothing. Lightroom and Capture One do not discover `<image>.<ext>.xmp` for proprietary RAW (see Facet docs/INTEROP.md); digiKam and darktable do.

## Idempotency and `--min-confidence`

Each run replaces that photo's `astro-id` rows in one transaction, so re-running gives the same tags. Raising the threshold (65 -> 80) removes the 65-79 tags on re-run; lowering it adds them back. Photos are not re-solved automatically when the config changes; run the backfill.

## Tests

`C:\Users\raysa\AppData\Local\Facet\venv\Scripts\python.exe -m pytest tests/test_facet_plugin.py tests/test_plugin_features.py` from the repo root (skips when Facet is not importable; set `FACET_DIR` to another checkout).
