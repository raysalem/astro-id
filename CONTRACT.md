# astro-id engine ↔ Facet plugin contract (v1.1)

Fixed before the engine and the plugin are built in parallel. Change it only with a version bump and both sides updated.

## Engine CLI (`uv run --project <repo> astro-id ...`)

```
astro-id solve <file-or-folder> [--min-confidence 65] [--offset +2559d11h08m]
               [--time ISO8601 [--timezone IANA]]
               [--time-tolerance 15m] [--site lat,lon[,elev_m]]
               [--overlay-dir DIR] [--json] [--no-overlay] [--no-fallback] [--no-results-file]
astro-id site <folder>          # interactive: Google Maps pin -> astro-id.json
```

- Flags override the nearest `astro-id.json` found walking up from each photo's folder.
- `--json`: print ONE JSON document to stdout: `{"contract": 1, "results": [<frame>, ...]}`. All logging goes to stderr.
- Exit code 0 when the run completed (even if some frames failed to solve); non-zero only for usage/environment errors.
- Inputs: JPG/JPEG/TIF/PNG directly; RAW (ARW, CR2, CR3, NEF, DNG...) is mapped to its same-stem JPG when present, otherwise decoded.

## `astro-id.json` (per photo folder, never in the repo)

```json
{
  "clock_offset": "+2559d11h08m",
  "time_tolerance_min": 15,
  "timezone": "America/Los_Angeles",
  "site": {"name": "Tierra del Sol", "lat": 32.6064, "lon": -116.3239, "elev_m": 1100}
}
```
`clock_offset` is added to EXIF `DateTimeOriginal` (camera local time) to get true local time in `timezone`.

## Frame result

```json
{
  "file": "C:/abs/path/DSC07610.JPG",
  "solved": true,
  "solver": "astap" | "astrometry.net" | null,
  "grade": "A" | "B" | "C" | null,
  "rms_px": 0.8,
  "stars": 143,
  "center_ra_deg": 300.1, "center_dec_deg": 40.2, "fov_deg": [84.0, 62.0],
  "true_time": "2026-11-07T20:34:00-08:00",
  "time_tolerance_min": 15,
  "time_source": "config" | "exif" | "facet",
  "best_fit_time": "2026-11-07T20:36:00-08:00" | null,
  "time_flag": null | "best_fit_outside_tolerance",
  "overlay": "C:/abs/path/astro-annotated/DSC07610_astro.jpg" | null,
  "error": null | "short message",
  "objects": [
    {
      "name": "M31 Andromeda Galaxy",
      "tag": "m31 andromeda galaxy",
      "kind": "galaxy" | "nebula" | "cluster" | "star" | "planet" | "moon" | "constellation" | "other",
      "confidence": 87,
      "tier": "high" | "likely" | "candidate",
      "x": 1234.5, "y": 678.9
    }
  ]
}
```

- `objects` lists EVERY identified object, all tiers; the consumer filters by `--min-confidence`.
- `tag` is already lowercase, no comma, ≤ 64 chars.
- `confidence` is an integer 0–100. Tiers: high ≥ 80, likely ≥ 65, candidate < 65 (the tier boundaries are fixed; `--min-confidence` only moves the tag cut).
- `x`, `y` are pixels in the full-resolution source image (origin top-left); `null` for constellations.
- Unsolved frame: `solved: false`, `objects: []`, `error` set.

A stub with realistic values lives at `tests/fixtures/contract_stub.json`.

## v1 additions (non-breaking, Facet as source of truth)

- `--time <ISO-8601>` (single file only; a folder is a usage error, exit 2): the true local time of that frame, overriding EXIF + offset. With an offset (`2026-11-07T20:34:00-08:00`) it is used as is; a naive value is read in `--timezone`, else the folder's `astro-id.json` timezone, else the zone inferred from the site, else the system zone. The result carries `"time_source": "facet"`; time-dependent IDs are not capped (the time is trusted).
- `--site lat,lon` without elevation looks it up on USGS EPQS (cached per coordinate rounded to 3 decimals); on failure 0 m is used and a warning is logged.
- Timezone: when `astro-id.json` has no `timezone` (absent or null) it is inferred from the site lat/lon (timezonefinder), else the system zone. `astro-id site` writes the inferred zone, never null.
- Consumers must treat unknown `time_source` values as opaque strings.

## v1 additions (non-breaking, 2026-11-10): tag hierarchy, tag policy, stacking sequences

Every object gains five fields; consumers must ignore fields they do not know.

```json
{"name": "Orion's Belt", "tag": "orion's belt", "kind": "asterism", "confidence": 90, "tier": "high",
 "x": 4100.0, "y": 2300.0,
 "level": 4, "magnitude": 1.69, "parent": "orion", "member_of": null, "taggable": true}
```

- `level`: 1 constellation, 2 Sun/Moon/planet, 3 catalog object (Messier/NGC/IC), 4 asterism (named star group), 5 named star.
- `kind` gains the value `"asterism"` (x/y = centroid of its member stars in frame). Consumers must treat unknown `kind` values as opaque.
- `magnitude`: V magnitude (asterism: its brightest member); `null` for constellations.
- `parent`: tag of the IAU constellation the object lies in (Roman 1987 boundaries); `null` for constellations and for asterisms spanning several constellations.
- `member_of`: tag of the cluster or asterism that subsumes the object (stars inside a cluster's radius and fainter than it; asterism member stars), else `null`.
- `taggable`: the engine's tag policy says this object should become a tag. A consumer tags `taggable && confidence >= min_confidence` and nothing else. Members of a group that is itself taggable and >= `--min-confidence` are not taggable.
- `tag`, `parent` and `member_of` follow the tag style: `plain` (`orion`, `jupiter`, `m45 pleiades`) or `kind` (`constellation/orion`, `planet/jupiter`, `moon`, `messier/m45 pleiades`, `ngc/...`, `asterism/orion's belt`, `star/sirius`).

Tag policy (flags override `astro-id.json` `"tag_policy": {...}` which overrides the defaults):

| Flag | json key | Default | Meaning |
|---|---|---|---|
| `--tag-levels 1,2,3,4` | `levels` | `[1,2,3,4]` | levels that may become tags |
| `--star-mag-limit M` | `star_mag_limit` | `null` (stars off) | turns level 5 on for named stars at least this bright (1.5 = the ~20 brightest) |
| `--max-catalog-mag M` | `max_catalog_mag` | `6.0` | faintest level-3 object to tag |
| `--tag-style plain\|kind` | `tag_style` | `plain` | tag spelling, see above |

Stacking sequences (folder runs; flags override `astro-id.json` `stack_mode`, `stack_gap_s`, `stack_tolerance`):

| Flag | json key | Default | Meaning |
|---|---|---|---|
| `--stack-mode representative\|all` | `stack_mode` | `representative` | `all` solves every frame |
| `--stack-gap S` | `stack_gap_s` | `120` | max seconds between consecutive frames of one sequence |
| (json only) | `stack_tolerance` | `0.02` | max pointing change, fraction of the frame diagonal |

A sequence = consecutive frames (EXIF time gap <= `stack_gap_s`) with the same lens, focal length, exposure and size. The middle frame is solved; each other frame is registered to it by star pattern (no ASTAP). When it registers within the tolerance it reuses the representative's objects (positions mapped through the registration) and its frame result has `"solver": "stack-copy"` and `"stack_of": "<representative file>"`; otherwise it is solved on its own.

- Every frame result gains `stack_of` (`null` unless `solver` is `"stack-copy"`). `solver` may be `"stack-copy"`; consumers must treat unknown `solver` values as opaque.
- Frame `stars` / `rms_px` of a stack-copy are the registration's matched-star count and residual; `grade` and `fov_deg` are the representative's; `true_time` is the frame's own.
- `astro-id-report.csv` gains columns `level, magnitude, parent, member_of, taggable, stack_of`. JSON, CSV and overlay keep listing every object (taggable or not).

## v1 additions (non-breaking, 2026-11-10): annotations, overlay label switch, solve source

- Every frame result gains `annotations`: the payload of `ANNOTATIONS.md` (version 1, source `astro-id`), or `null` when the frame is unsolved (or the build failed). All objects at all levels, stars to mag 6.5, stick figures as sampled, clipped, SIP-corrected `lines` segments in full-resolution pixels (0.1 px), `member_of` = id of the group item, under ~500 KB. A stack-copy frame carries its own payload (representative's solution mapped through the registration).
- Every frame result gains `solve_source`: `"jpg"` or `"raw"` (the pixels the solver read; `null` when unsolved before that point). Coordinates are always in the JPG (Facet photo) pixel space.
- New flags: `--overlay-scores` (overlay JPG labels carry the confidence; default off = plain names; the legend keeps the tier colour key) and `--solve-source jpg|raw|auto` (default `jpg`; also `astro-id.json` `solve_source`).
- Consumers must ignore unknown fields.

## v1.1 (non-breaking, 2026-11-11): quality metrics, culling, astro session

The JSON document keeps `"contract": 1` (the major version; v1.1 only adds fields). Consumers must ignore unknown fields.

### Frame result additions

- `quality`: per-frame metrics for culling, or `null` (`--no-quality`, or the image could not be read). Present on solved, unsolved and stack-copy frames. Measured on the frame's own pixels (the JPG, or a lone RAW), never on the solve source.
- `best_fit_delta_min`: `best_fit_time - true_time` in minutes (1 decimal), `null` when there is no best fit (today only a Moon in the frame pins the time).
- `--timezone` now also applies to the camera-local EXIF time + offset (before: only to a naive `--time`). Precedence: `--timezone` > `astro-id.json` `timezone` > inferred from the site > system zone.
- New flag `--no-quality`.

```jsonc
"quality": {
  "version": 1,
  "detected": 12298,        // stars detected (5 sigma, half-size image)
  "matched": 1043,          // plate-solve catalog matches; stack-copy: registration pairs; null when unsolved
  "measured": 150,          // isolated, unsaturated stars measured for size and shape
  "detect_scale": 2.0,      // detection smoothing (half-size px) whose kernel matched the stars; > 2 = large discs
  "fwhm_px": 6.99,          // median half-flux diameter, full-resolution px (= FWHM for a Gaussian; the disc for defocus)
  "fwhm_arcsec": 470.3,     // fwhm_px x pixel scale at the centre (null when unknown)
  "elongation": 1.163,      // median major/minor axis ratio (1 = round; trailing / wind raise it)
  "roundness": 0.86,        // 1 / elongation
  "background": 41.0,       // sky level, 8-bit DN on the half-size image (cells holding stars only)
  "noise": 4.45,            // sky noise (robust sigma), same units
  "grade": "B",             // A: fwhm <= 6 px, elongation <= 1.35, >= 500 stars; B: <= 10 px, <= 1.8, >= 100; else C
  "measured_on": "jpg"      // "jpg" | "raw"
}
```

Size and shape use stars within 60 % of the half diagonal when there are at least 15 (wide-angle corner coma is not trailing). The grade is absolute and lens-dependent; session-relative verdicts are the cull rule's job.

### Culling (`astro_id/cull.py`, pure functions)

Rule, from (first found) the session's `--cull-rule`, the folder's `astro-id.json` `"cull"`, or the defaults (the Facet plugin validates the same shape under `astro_id.cull` for the session page):

```json
{"enabled": true, "order": ["focus", "trailing", "stars"],
 "thresholds": {"focus_ratio": 1.5, "focus_px": null, "trailing_review": 1.6, "trailing_reject": 3.0,
                "stars_ratio_review": 0.4, "stars_min": 20}}
```

| Check | Verdict |
|---|---|
| `focus` | `fwhm_px >= focus_ratio x reference.fwhm_px` (or `>= focus_px`) -> `review`. **Never `reject`**: defocused frames are kept for stacking. |
| `trailing` | `elongation >= trailing_reject` -> `reject`; `>= trailing_review` -> `review` |
| `stars` | `detected < stars_min` -> `reject`; `< stars_ratio_review x reference.detected` -> `review` |
| `solve` | frame not plate-solved -> `review` (off by default) |

`order` lists the checks that run, most important first; reasons come out in that order. A threshold set to `null` is off. Missing metrics -> `review` ("no quality metrics"). `reference` (session baseline) = lower quartile of `fwhm_px` (the best-focused frames) and median of `detected` over the session's frames.

`cull_rank(metrics, rule, reference, solved)` ->

```json
{"decision": "keep | review | reject",
 "reasons": ["focus: star size 86.3 px is 15.2x the session best 5.7 px (defocused; kept for stacking)"],
 "checks": [{"check": "focus", "level": "review", "value": 86.26, "threshold": 8.5, "reason": "..."}],
 "enabled": true}
```

Nothing in the engine writes a library's cull/reject flags; a caller applies verdicts after the user confirms.

### Astro session (`astro_id/session.py`, `astro-id session`)

Library functions (no photo-library access; per-photo facts come in as `hints`):
`SessionOptions(offset, tolerance_min, source, site, tz, min_confidence=65, hints={path: {"site", "time"}}, cull, overlay_dir, no_overlay, no_fallback, no_quality, overlay_scores, stack_mode, stack_gap, extra_flags, include_results)`,
`time_preview(paths, opts, emit)`, `run_session(paths, opts, emit, save_config=False)`, `save_folder_config(folders, opts)`, `recull(metrics_by_path, rule, solved_by_path)`, `plan_jobs(paths, opts)`.

```
astro-id session (PATH ... | --paths-file F) [--hints-file F] [--preview]
                 [--offset +2559d11h08m] [--tolerance-min 5] [--source jpg|raw|auto]
                 [--site lat,lon[,elev_m]] [--tz America/Los_Angeles] [--min-confidence 65]
                 [--save-folder-config] [--cull-rule JSON|@file] [--results]
                 [--overlay-dir DIR] [--no-overlay] [--no-fallback] [--no-quality] [--overlay-scores]
                 [--stack-mode representative|all] [--stack-gap S] [-v]
astro-id cull --metrics-file F [--cull-rule JSON|@file]
```

- `--paths-file`: JSON list of paths, or `{"paths": [...], "hints": {path: {"site": "lat,lon[,elev]", "time": "ISO-8601"}}}`. `--hints-file`: the hints object alone. A hint `time` is that photo's true local time (naive = read in the session timezone); a hint `site` is that photo's own position.
- Precedence: time = hint time > EXIF + offset (`--offset` > `astro-id.json`) > EXIF as is; site = hint site > `--site` > `astro-id.json` > none; timezone = `--tz` > `astro-id.json` > inferred from the site > system; tolerance, source, cull rule = flag > `astro-id.json` > default (15 min, jpg, built-in rule).
- `--save-folder-config` writes `clock_offset`, `time_tolerance_min`, `timezone` (the ones given) into each photo folder's own `astro-id.json` before solving (merged; a folder that only inherited a parent's file gets a copy of it, so the parent's site keeps applying). Event `config_saved` per file written.
- Jobs: photos are grouped per (folder, site) and solved together (stacking sequences intact); a photo with a hint time runs alone with `--time`. A RAW path is solved through its same-stem JPG and reported under the RAW path.
- stdout = JSON lines, one event per line, flushed; logs on stderr. Exit 0 when the session ran (frames may fail), 2 for a bad option (after an `error` event `{"event": "error", "error": "..."}`).

Events, `--preview`:

```json
{"event": "start", "mode": "preview", "total": 18, "session": 1}
{"event": "preview", "index": 0, "path": "...", "camera_time": "2019-11-05T09:31:59", "offset": "+2559d11h08m",
 "offset_source": "session | config | null", "true_time": "2026-11-07T20:39:59-08:00",
 "window_start": "2026-11-07T20:34:59-08:00", "window_end": "2026-11-07T20:44:59-08:00", "tolerance_min": 5,
 "time_source": "config | exif | facet", "timezone": "America/Los_Angeles",
 "tz_source": "session | config | site | system",
 "site": {"lat": 32.6, "lon": -116.3, "elev_m": 1128.8, "name": "..."},
 "site_source": "photo | session | config | none", "raw_companion": ".../DSC07610.ARW",
 "solve_source": "jpg | raw", "config": ".../astro-id.json", "warnings": ["..."], "error": null}
{"event": "done", "mode": "preview", "total": 18, "errors": 0, "elapsed_s": 0.4}
```

Events, solve:

```json
{"event": "start", "mode": "solve", "total": 18, "session": 1}
{"event": "config_saved", "path": ".../astro-id.json"}
{"event": "photo", "index": 3, "path": "...", "file": "...", "status": "solved | failed | stack-copy",
 "objects": 15, "grade": "A", "stars": 2452, "rms_px": 0.4, "solve_source": "jpg", "stack_of": null,
 "true_time": "...", "time_source": "config", "tolerance_min": 5, "best_fit_time": null, "best_fit_delta_min": null,
 "time_flag": null, "quality": {}, "overlay": "...", "error": null, "result": {}}
{"event": "done", "mode": "solve", "total": 18, "solved": 14, "stack_copies": 1, "failed": 3, "elapsed_s": 412.0,
 "time": {"tolerance_min": 5, "best_fit_frames": 0, "max_abs_delta_min": null, "suggest_tolerance_min": null,
          "outside_tolerance": 0},
 "cull": {"reference": {"fwhm_px": 5.7, "detected": 7066, "frames": 18}, "rule": {},
          "frames": {"<path>": {"decision": "review", "reasons": ["..."]}}}}
```

- `photo` events come as frames finish (a sequence's representative first); `index` = position in the input. `objects` = taggable objects at or above `--min-confidence`. `quality` = the frame's `quality` object; `result` (the full frame result) only with `--results`.
- `time.suggest_tolerance_min` = ceil(max |best_fit_delta_min| + 2) when that is tighter than the tolerance used; `null` otherwise (no Moon-pinned frame).
- `astro-id cull` prints one JSON document `{"reference", "rule", "frames": {path: <cull_rank result>}}` from `{path: quality}` or `{"metrics": {...}, "solved": {...}}`; nothing is solved.

### Tolerance and solving cost

The tolerance never changes what ASTAP or the refit does (the plate solve is blind and time-free); identification samples the window at 5 points whatever its width, so cost is flat. A tighter window only sharpens time-dependent scoring: worst-case altitude over the window (horizon margin), planet/Moon position spread, and the `best_fit_outside_tolerance` flag. `--time-tolerance` / `--tolerance-min` is honoured end to end (frame `time_tolerance_min`, legend `±5 min`, CSV `tolerance_min`, session events).

## Facet sidecar commands (2026-11-11, docs only; the contract above is unchanged)

The consumer of this contract is now the sidecar in `astro_id/facet/` (it imports `astro_id.session` in process),
not an in-process Facet plugin; the plugin and its `plugins.astro_id` config block were removed, so mentions of
"the Facet plugin" above describe history. The sidecar talks to Facet only over HTTP (`facet_plugin/README.md`).

```
astro-id facet sync  [--facet-url URL] [--path-prefix P] [--category astro|''] [--tag T] [--album ID]
                     [--since YYYY-MM-DD|last] [--force] [--limit N] [--spool FILE] [--dry-run] [--json]
                     [--legacy-db PATH --facet-dir DIR] [--no-annotations] [--ledger FILE] [--path-map FACET=LOCAL]
                     [--min-confidence 65] [--offset ...] [--tolerance-min M] [--tz ZONE] [--source jpg|raw|auto]
                     [--site lat,lon[,elev]] [--overlay-dir DIR] [--no-overlay] [--no-fallback]
                     [--stack-mode representative|all] [--engine-flag FLAG ...]
astro-id facet serve [--facet-url URL] [--port N] [--no-browser] [--legacy-db PATH --facet-dir DIR] [--ledger FILE]
astro-id facet status [--facet-url URL] [--ledger FILE]
astro-id facet undo   [--facet-url URL] [--path-prefix P] [--dry-run] [--legacy-db PATH --facet-dir DIR]
astro-id facet adopt  --legacy-db PATH [--path-prefix P] [--dry-run]     # TEMPORARY one-time migration
```

- Credential (environment only): `FACET_EDITION_PASSWORD`, or `FACET_USERNAME` + `FACET_PASSWORD` (multi-user),
  or `FACET_TOKEN` (bearer, used as is); `FACET_URL` (default `http://127.0.0.1:5000`). Without one, nothing is
  written.
- Per photo the sidecar passes Facet's facts as session `hints`: its GPS as `site`; its capture date as `time` only
  when `date_manually_set` (a corrected date). Tags written = `plate-solved` / `plate-solve failed` + every object
  with `taggable` and `confidence >= --min-confidence`, by level, under Facet's 50-tag cap (other tags first).
- `sync --json` prints the session events (above) plus `{"event": "sync_start", ...}`, one
  `{"event": "written", "path", "status", "add", "remove", "kept", "skipped", "dropped", "annotation"}` per photo and
  `{"event": "sync_done", ...}`. Exit 0 when the run completed, 2 for a usage, login or connection error.
- Ledger (`%LOCALAPPDATA%\astro-id\facet-ledger.sqlite`, `$XDG_DATA_HOME/astro-id/` elsewhere, or
  `$ASTRO_ID_LEDGER`): per Facet URL and photo path the tags astro-id added, the annotation payload hash and how it
  was stored, the last status and the Facet version; plus the `--since last` watermark (newest Facet capture date
  seen). A re-run removes only ledger tags; a tag someone else put on the photo is never claimed or removed.

## Facet add-on (2026-11-11, PLUGIN_BOUNDARY.md v0.2; the engine contract above is unchanged)

astro-id is the first Facet add-on. The boundary spec is the contract; this section records astro-id's side of it.

```
astro-id facet install   --facet-data DIR [--uv PATH] [--project DIR]   # writes DIR/addons/astro-id/addon.json only
astro-id facet uninstall --facet-data DIR                               # deletes it (and the empty folder)
astro-id facet serve --addon [--ledger FILE] [--no-annotations] [--path-map FACET=LOCAL]   # started by Facet
```

**Manifest** (spec §4; `argv[0]` and `--project` are absolute, resolved at install time):

```json
{"id": "astro-id", "name": "Astro", "version": "<package version>", "icon": "star", "facet_api": 1,
 "start": {"argv": ["<abs>/uv.exe", "run", "--project", "<abs>/astro-id", "astro-id", "facet", "serve", "--addon"],
           "port": "auto", "health": "/health"},
 "ui": {"path": "/ui/"},
 "slots": ["sidebar", "import_hook"],
 "permissions": ["photos.read", "tags.write", "annotations.write", "gps.write"]}
```

**Process (spec §5):**

- Environment in: `FACET_URL`, `FACET_TOKEN`, `FACET_ADDON_ID`, `FACET_LAUNCH_SECRET` (>= 16 chars), optional
  `FACET_PORT`. Missing URL / id / secret = exit 2. `--legacy-db` is refused in add-on mode.
- Credential: `addon_credentials(id)` in `astro_id/facet/client.py`. `FACET_TOKEN` is used as the bearer as is.
  TEMPORARY (fork prototype, spec §5): without it, a password from `$ASTRO_ID_ADDON_ENV` or
  `%LOCALAPPDATA%\astro-id\addon-credentials.env`, then the process env; otherwise read-only. The sidebar shows which.
- Binds `127.0.0.1:0` (or `FACET_PORT`) and writes exactly one stdout line, `FACET_ADDON_PORT=<n>\n`, flushed,
  before it contacts Facet. All logging goes to stderr.
- `GET /health?c=<1..512 chars>` (no launch header needed) -> `{"addon": FACET_ADDON_ID, "ack": hex
  HMAC-SHA256(FACET_LAUNCH_SECRET, c)}`; no `c` -> 400.
- Every other request needs `X-Facet-Launch == FACET_LAUNCH_SECRET` (constant-time) -> else 403. Host must be
  loopback (403). The path is decoded once and refused (400) on `..` / `.` segments, `\`, NUL or an empty segment.
- Routes: `GET /ui` -> 308 `Location: ui/` (relative); `GET /ui/` the page (bridge script inlined);
  `GET|POST /ui/api/<name>` the page's API; `OPTIONS` -> 204; PUT/DELETE/PATCH -> 405; anything else 404.
  `X-Forwarded-Prefix` matching `/addons/<id>` is recorded (shown in `api/state` as `mount`); all URLs are relative,
  so nothing depends on it.
- Response headers: `Cache-Control: no-store`, `nosniff`, `Referrer-Policy: no-referrer`, a CSP equal to the
  proxy's (`sandbox allow-scripts; default-src 'none'; ...`). Never `Set-Cookie`; no cookie is read.
- Lifetime: the add-on exits when uv (the process Facet spawned) or Facet ends (`astro_id/facet/lifeline.py`;
  on Windows terminating uv.exe does not end its children). POSIX: `killpg` from Facet, plus a `getppid` check.

**Page API (`/ui/api/...`, JSON; the standalone page uses the same names under `/<secret>/`):** `state`, `albums`,
`job?after=N` (GET); `photos` (manual loader), `scope`, `gps`, `preview`, `save-config`, `solve`, `recull`,
`review-tags` (POST); `thumb?path=&size=` (GET, image bytes).

- `POST scope` maps the bridge scope to the step-1 photo set:
  - `{"kind": "import", "run_id": "<[A-Za-z0-9_.:-]{1,128}>", "category": "astro"|""}` ->
    `GET /api/photos?scan_run=<run_id>[&category=...]` (all frames);
  - `{"kind": "view", "query": "<query string <= 2 KB>" | {key: value}}` -> `GET /api/photos` with that query minus
    `page`, `per_page`, `sort`, `dir`, `nonce` and the `hide_*` / `collapse_stacks` toggles (astro-id wants every
    frame, in capture order);
  - `{"kind": "selection", "paths": [<= 500]}` -> `GET /api/photo` per path (unknown paths dropped).
  - At most 2000 photos. Returns the step-1 listing plus `scope`.
- `GET thumb` serves Facet's `GET /thumbnail?path=&size=` (size 16..640) for a photo in the current set only (404
  otherwise), fetched with the add-on token.

**Writes:** tags via `POST /api/photos/batch_manual_tags` / `DELETE /api/photo/manual_tags` + the ledger.
`POST /api/photos/plugin_tags` is feature-detected first (an empty-body probe; 422 = present); while its body is
not pinned in the spec, a present route only produces a sidebar note. Annotations via `PUT /api/photo/annotations`
when the probe answers 422 (with an add-on token a 403 means "not granted" and counts as absent), else skipped with
a sidebar note. GPS via `PUT /api/photo/gps`. Never the cull or reject flags.

**Bridge (spec §8, `astro_id/facet/static/bridge.js`):** the nonce comes from `?nonce=` (8..256 chars of
`[A-Za-z0-9_.~-]`), lives in memory only and goes out as `X-Addon-Nonce` on every fetch (`credentials: "omit"`,
relative URLs only). On load the panel posts `{type: "ready", v: 1, nonce}` to `window.parent` (target `"*"`). It
accepts only `{type: "context", v: 1, nonce}` from `window.parent` with that nonce and keeps `route`, `album_id`,
`photo`, `selection` (<= 500) and `scope`. The first context loads step 1; a later context with a different scope
only offers "Load the new photos", so a session in progress is never wiped. Panel -> host:
`show_photos {paths}`, `open_photo {path}`, `refresh {paths}` (after GPS, solve writes and review tags), `close`;
each carries `v: 1` and the nonce. No storage, cookies, popups, downloads, form submits or modal dialogs.

## CLI gaps closed (2026-11-15, non-breaking; the contract number stays 1)

### Results file

`astro-id solve` writes `astro-id-results.json` beside `astro-id-report.csv` (in `<folder>-annotated`, or `--overlay-dir`; never next to the photos): `{"contract": 1, "results": [<frame>, ...]}`, exactly the document `--json` prints, one entry per frame. Like the CSV it is merged across runs: a run replaces only the entries of the files it solved and keeps the others (sorted by `file`). `--no-results-file` turns it off. The log line `results: <path>` follows `report: <path>`. `astro-id session` and the Facet sidecar / add-on call the engine in process and never write it (they get the same data as events); nothing new appears in their output folders.

### Frame `warnings`

Every frame result gains `warnings`: a list of strings (empty when there is nothing to say). Consumers must ignore unknown fields as before. Each distinct message is also logged once per run on stderr.

- `no clock offset configured: ...`: `time_source` is `"exif"`, so the EXIF time is used as is and Sun, Moon and planets are capped at candidate.
- `no site (position): ...`: see the next section.
- `no EXIF DateTimeOriginal: ...`: the current clock time is used (see below).

### Sun, Moon and planets: what happens without a trustworthy time or a site (verified in `identify.py`, `config.py`)

- **No clock offset** (no `--offset`, no `clock_offset` in `astro-id.json`, no `--time`; `time_source` `"exif"`): Sun, Moon and planets are still identified, at the raw EXIF time, and their confidence is capped at 64, i.e. tier `candidate`. They stay in the JSON, the CSV and the labeled JPG; with the default `--min-confidence 65` they never become object tags. Any `clock_offset` key counts as configured, even a blank one ("the camera clock is right"), and then the cap is lifted. `--time` (`time_source` `"facet"`) also lifts it.
- **No EXIF capture time and no `--time`**: the frame is solved and the same bodies are computed for the current clock time of the machine (they are not dropped), with the candidate cap. Both a `warnings` entry and `error` say so.
- **No site** (no `--site`, no `site` in `astro-id.json`, no per-photo position from Facet): the bodies are computed from the geocentre, so the Moon can be up to about 1 degree off, and no altitude or horizon check is made (a body below the horizon is not ruled out; the horizon margin counts as 1). Because of that their confidence is capped: **planets and the Sun at 79, the Moon at 70**, i.e. tier `likely` at best, never `high`, so they cannot become a certain object tag. 79 is the top of the `likely` band (high starts at 80); the Moon gets the stricter 70, close to the 65 floor, because its parallax (up to about 1 degree, twice its disc) is the one error that can put it in the wrong place. The caps combine with the clock cap (the lower wins, so no offset still means 64). The `no site (position)` warning stays and now mentions the cap. Stars, constellations, asterisms and deep-sky objects are unaffected. With a site (`--site`, `astro-id.json`, or the per-photo position the Facet add-on passes) nothing changes: full confidence. Additive in v1.1: confidence values for bodies in site-less runs are lower than before; no field was added or renamed.
- **No default site is ever assumed or written** (changed: the old Tierra del Sol suggestion is gone). `astro-id site <folder>` suggests, in order: the folder's own saved site, then the site this user last saved on this machine (`last_site.json` in the per-user data folder, `ASTRO_ID_DATA` or `%LOCALAPPDATA%\astro-id\data`, written only after a confirmed save), else nothing: the coordinates prompt then requires coordinates or a Maps URL and Enter alone saves nothing. The map opens on a neutral world view when no site is known.
- The timezone is separate: with no site and no `timezone` it falls back to the system zone.

### Exit codes (read from the code that returns them)

Every subcommand also exits 2 when the command line itself cannot be parsed. The same table is printed in each command's `--help`.

| Command | Exit | Meaning |
|---|---|---|
| `astro-id solve` | 0 | the run completed (frames that did not solve are in the report, not an error) |
| `astro-id solve` | 2 | usage error: target not found, a bad flag value, or no images in the folder |
| `astro-id label` | 0 | labeled (or every photo already had its output) |
| `astro-id label` | 1 | nothing labeled (no photo solved) |
| `astro-id label` | 2 | setup or usage problem: target not found, a bad option, no photos, or ASTAP not found |
| `astro-id check` | 0 | all required checks passed |
| `astro-id check` | 1 | a required check failed (marked with x) |
| `astro-id site` | 0 | saved |
| `astro-id site` | 1 | not saved (you answered no at the Save? prompt) |
| `astro-id site` | 2 | the folder does not exist |
| `astro-id setup` | 0 | reference data is ready and ASTAP was found |
| `astro-id setup` | 3 | ASTAP was not found (set ASTAP_PATH) or a reference dataset is not ok |
| `astro-id session` | 0 | the session ran (frames may have failed; see the events) |
| `astro-id session` | 2 | a bad option or no photos (after an error event on stdout) |
| `astro-id cull` | 0 | verdicts printed |
| `astro-id cull` | 2 | bad metrics file or cull rule |
| `astro-id facet sync` | 0 | the run completed |
| `astro-id facet sync` | 2 | usage, login or connection error |
| `astro-id facet status` | 0 | ledger printed |
| `astro-id facet status` | 2 | usage, login or connection error |
| `astro-id facet undo` | 0 | done (or dry run) |
| `astro-id facet undo` | 2 | usage, login or connection error |
| `astro-id facet adopt` | 0 | done (or dry run) |
| `astro-id facet adopt` | 2 | usage, login or connection error |
| `astro-id facet serve` | 0 | stopped normally (Ctrl+C, or Facet ended) |
| `astro-id facet serve` | 2 | missing or bad environment / connection error |
| `astro-id facet install` | 0 | manifest written |
| `astro-id facet install` | 2 | Facet data folder not found, uv not found, or a bad --project |
| `astro-id facet uninstall` | 0 | manifest removed (or there was none) |
| `astro-id facet uninstall` | 2 | Facet data folder not found |

### `facet install`

The manifest carries the `network` key unless `--older-facet` is given. Whether a Facet accepts it cannot be known offline, so after a normal install the command prints one line on stderr: `If Facet refuses the add-on, run again with --older-facet`.
