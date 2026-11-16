# astro-id reference

The detail behind the [README](../README.md): every command, option and rule. For a guided walk-through see the
[manual](MANUAL.md); for the machine formats see [CONTRACT.md](../CONTRACT.md) (JSON output, session events, Facet
add-on wiring) and [ANNOTATIONS.md](../ANNOTATIONS.md) (the overlay payload). Minimum versions are in
[REQUIREMENTS.md](REQUIREMENTS.md).

**Contents:** [ASTAP and reference data](#astap-and-reference-data) ·
[Command-line usage](#command-line-usage) · [astro-id.json](#per-folder-settings-astro-idjson) ·
[Time and tolerance](#time-and-tolerance) · [Annotations payload](#annotations-payload-for-the-facet-viewer) ·
[Quality and culling](#quality-metrics-and-culling) · [Astro session](#astro-session-astro-id-session) ·
[RAW as solver input](#raw-as-the-solver-input-solve_source) · [All options](#all-options) ·
[Facet sidecar](#facet-sidecar-astro-id-facet-) · [Facet add-on](#facet-add-on-sidebar-inside-facet) ·
[How it solves](#how-it-solves) · [Confidence scale](#confidence-scale) ·
[Tag hierarchy and tag policy](#tag-hierarchy-and-tag-policy) · [Stacking sequences](#stacking-sequences) ·
[Moon features](#moon-features-astro-id-moon) · [astrometry.net fallback](#optional-fallback-astrometrynet)

## ASTAP and reference data

astro-id uses the [ASTAP](https://www.hnsky.org/astap.htm) command-line solver (MPL-2.0, by Han Kleijn) and its
star databases. **W08** covers fields of roughly 20 degrees and up (wide-angle lenses). For telephoto frames (field
under ~20 degrees, e.g. 85-200 mm on full frame) also install **D50**. Install steps per platform are in the
[README](../README.md#install).

astro-id finds ASTAP through the `ASTAP_PATH` environment variable (the executable or its folder), then
`%LOCALAPPDATA%\astap`, `%ProgramFiles%\astap` and `%ProgramFiles(x86)%\astap` (Windows), then `astap_cli` or
`astap` on `PATH`, then `/opt/astap` (where the Linux packages install). The star database is taken from `ASTAP_DB`
or the folder of the executable (a database is present when its `*_0101.*` file is there). On macOS set both:
`ASTAP_PATH=/Applications/ASTAP.app/Contents/MacOS/astap` and `ASTAP_DB=/usr/local/opt/astap`.

`uv run astro-id setup` downloads the reference catalogs and reports the ASTAP path and databases (exit 3 when ASTAP
or a dataset is missing); `uv run astro-id check` compares the whole setup with the stated minimums
([REQUIREMENTS.md](REQUIREMENTS.md)) and exits 0 when every required check passes. The catalogs are cached in
`%LOCALAPPDATA%\astro-id\data` on Windows and `~/.cache/astro-id/data` elsewhere (override with `ASTRO_ID_DATA`).
Sources and licenses: [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Command-line usage

```powershell
uv run astro-id solve <file-or-folder> [--min-confidence 65] [--offset +2559d11h08m]
                     [--time-tolerance 15m] [--site lat,lon[,elev_m]]
                     [--overlay-dir DIR] [--json] [--no-overlay] [--no-fallback] [--no-results-file] [-v]
                     [--tag-levels 1,2,3,4] [--star-mag-limit M] [--max-catalog-mag 6.0]
                     [--tag-style plain|kind] [--stack-mode representative|all] [--stack-gap 120]
                     [--solve-source jpg|raw|auto] [--overlay-scores]
uv run astro-id label <photo-or-folder> [--out labeled|sidecar|both] [--to DIR]   # the simplest way
uv run astro-id site <folder>     # pick the observing site on Google Maps -> astro-id.json
uv run astro-id setup             # download data, check ASTAP
uv run astro-id check             # check the minimum requirements (--facet URL, --offline, --json)
```

`astro-id label` ([SIMPLE.md](SIMPLE.md)) writes `<name>_astro.jpg` next to each photo (or `--to DIR`), or with
`--out sidecar` a `<name>.astro.json`; `astro-label.cmd` (drag and drop, Windows) and `astro-label.sh` wrap it.
Exit codes of every command: [CONTRACT.md](../CONTRACT.md#exit-codes-read-from-the-code-that-returns-them).

- A folder is scanned for JPG/JPEG/TIF/PNG and RAW. A RAW file is replaced by its same-stem JPG when
  there is one (RAW+JPG pairs count once); a lone RAW is decoded.
- Overlay labels are plain names ("Uranus"); `--overlay-scores` adds the confidence ("Uranus 100"). The legend keeps
  the tier colour key. The live, filterable overlay is drawn by the Facet viewer from the `annotations` payload
  (see below); the JPG is the static copy.
- Overlays (`<stem>_astro.jpg`, 3000 px wide), `astro-id-report.csv` and `astro-id-results.json` (the `--json`
  document, merged across runs per file; `--no-results-file` skips it) go to `--overlay-dir`, default the sibling
  folder `<folder>-annotated`. Originals are never written to. Facet add-on and sidecar runs do not write the
  results file.
- `--json` prints one document `{"contract": 1, "results": [...]}` to stdout (see [CONTRACT.md](../CONTRACT.md));
  logs go to stderr. Each frame carries `warnings` (no clock offset, no site, no EXIF time). Exit code is 0 when
  the run completed, even if some frames did not solve.

### Per-folder settings: `astro-id.json`

```json
{
  "clock_offset": "+2559d11h08m",
  "time_tolerance_min": 15,
  "timezone": "America/Los_Angeles",
  "site": {"name": "Dark site", "lat": 32.6, "lon": -116.3, "elev_m": 1100}
}
```

The nearest `astro-id.json` walking up from each photo's folder applies; command-line flags override it.
`clock_offset` is added to EXIF `DateTimeOriginal` (camera local time) to get the true local time in
`timezone` (system zone when absent). Offsets read like `+2559d11h08m`, `-1h30m`, `90m` or `2559d 11:08`.

`astro-id site <folder>` opens Google Maps in Chrome at the folder's site (a neutral world view when none is
known), asks you to paste
`lat, lon` (right-click the spot in Maps and click the first line to copy it) or a Maps URL, looks up
the elevation from USGS EPQS (US only; you are asked otherwise), asks for a name, confirms, and
writes/merges `astro-id.json` (keeping any clock offset, tolerance and timezone already there). The suggested
site is the folder's own, else the last site saved on this machine (`last_site.json` in the data folder, written
only after a confirmed save); with neither it suggests nothing and asks for coordinates. No default site is ever
assumed or written.

### Time and tolerance

Camera clocks drift. Every time-dependent check (planet and Moon positions, above-horizon, horizon
margin) is evaluated across `[t - tolerance, t + tolerance]` and the **worst case** is scored.
The horizon is checked only when the time is confirmed (an offset, even `+0m`, `--time` or a Facet-corrected date),
a site is known and the photo agrees: a solved frame is its own proof that the stars the fit matched were up, so if
25% or more of them are more than 2 deg below the horizon at every sampled time (fewer than 3 matched: the field
centre below -10 deg), the clock or the site is wrong. Then the frame gets a `warnings` entry ("this frame's sky
was below the horizon at the recorded time ...") and `time_hint` (`code` `sky_below_horizon`, `time`,
`field_alt_deg`, `stars_below_fraction`), no horizon check is made, and the Sun, Moon and planets are capped at
*candidate*. A star the fit matched is never dropped or lowered by the horizon; with a trusted time the check still
rules out unmatched catalog objects below the horizon (behind the ground) and lowers them within 3 deg of it. When the
Moon is found in the frame its position pins the time (it moves ~0.5 deg per hour against the stars):
`best_fit_time` is reported and `time_flag` is set if it falls outside the tolerance. Without a clock
offset (`--offset`, or a `clock_offset` key in `astro-id.json`, even `0`) or a true time (`--time`, or a date
corrected in Facet), EXIF time is used as-is (`time_source: "exif"`) and the Sun, Moon and planets are capped at
*candidate* (confidence 64; `EXIF_TIME_CAP` in `astro_id/confidence.py`). They are still identified, never
dropped. Without a site (no `--site`, no `site` in `astro-id.json`, no per-photo position from Facet) the bodies are
computed from the geocentre with no horizon check, and capped at 79 (the Moon at 70): *likely* at best. The lower
cap wins when both apply. Stars, constellations and deep-sky objects are unaffected by either. The caps are
ceilings: a body predicted in the frame with no source at that spot has its detectability halved, so it scores 50
at most (with a wrong clock, the usual case).

## Annotations payload (for the Facet viewer)

Every solved frame result carries `annotations` (null when unsolved): the overlay as data, in the format fixed in
`ANNOTATIONS.md`. All objects at all levels are in it (planets, Moon, Sun, Messier/NGC/IC, asterisms, constellations,
named stars to magnitude 6.5) with their confidence and magnitude, so the viewer filters live by confidence, group
and magnitude without re-solving. Constellation and asterism stick figures are `lines` items: each segment is
sampled along the sky great circle (about every 1.5 degrees), projected through the SIP-corrected solution so it
bends with the lens, clipped to the frame, in full-resolution pixels rounded to 0.1 px. `member_of` is the id of
the group item a star belongs to. The payload stays under 500 KB (lowest-confidence items are dropped first if not).
The legend reads like `2026-11-07 21:57 PST +-15 min . ASTAP grade A . 2452 stars`.

## Quality metrics and culling

Every frame result carries `quality` (CONTRACT.md v1.1): detected and matched star counts, focus as the median
half-flux diameter of isolated unsaturated stars (`fwhm_px`, full-resolution px; equals the FWHM for a sharp star
and measures the disc of a defocused one), trailing as the elongation (major/minor axis) of the direction the
stars share, sky background and noise, and an A/B/C quality grade. Stars are detected at three smoothing scales:
a sharp frame is measured at the finest, a badly defocused one on whole discs, not fragments. `--no-quality` skips it
(about 5-15 s per 61 MP frame, mostly JPEG decoding).

Detection works at the solve's scale (the frame's own pixels below a 6000 px long edge, half size above) after
subtracting a background mesh interpolated from its cell centres, so the steep sky gradient of a twilight frame is
not taken for noise. Size and shape prefer clean stars (peak at least 30x the local noise). When the finest
well-supported scale finds compact stars the frame is sharp and that scale is used, so Milky Way clumps found at a
coarse scale cannot read as defocus; otherwise the scale whose kernel best fits the stars wins. Elongation counts
only the direction the stars share, per region of a 3 x 3 grid: trailing and wind shake have one; the grain of a noisy
frame does not. The grade and the `stars` check count max(detected, matched), so a frame the plate solve matched
hundreds of stars in is never "too few stars". Twilight frames still hold fewer stars than dark ones: in a mixed
session they can be `review` for stars (the reason says so), never `reject` when they solved well. Metrics carry
`version` 2; re-culling a session that mixes versions warns that the numbers are not comparable.

`astro_id/cull.py` turns the metrics into `keep | review | reject` with reasons against the session's own
baseline (best-focus quartile, median star count). Defocused frames are `review`, never `reject` (stacking).
The rule is configurable: `{"enabled", "order": ["focus", "trailing", "stars", "solve"], "thresholds": {...}}`
in `astro-id.json` `"cull"` or `--cull-rule`. Nothing writes a photo library's cull flags.

## Astro session (`astro-id session`)

The confirm-then-solve flow for a night's photos, as JSON lines on stdout (schema in CONTRACT.md v1.1):

```powershell
uv run astro-id session --paths-file photos.json --preview --offset +2559d11h08m --tolerance-min 5 --tz America/Los_Angeles
uv run astro-id session --paths-file photos.json --offset +2559d11h08m --tolerance-min 5 --source jpg --save-folder-config
uv run astro-id cull --metrics-file quality.json --cull-rule '{"order": ["focus", "stars"]}'
```

`--preview` reports per photo the EXIF camera time, the corrected time and its window, where the site and
timezone come from, and whether a RAW twin exists, without solving. The solve run emits `start`, one `photo` event
per frame as it finishes (status, objects, grade, quality, best-fit time), and `done` with the session's cull
verdicts and a suggested tighter tolerance when the Moon pinned the time. Per-photo facts a photo library holds
(its GPS, a corrected capture time) are passed as `hints`; the session never reads a library itself. The same
functions are importable (`astro_id.session`).

## RAW as the solver input (`solve_source`)

`jpg` (default) solves the JPG. `raw` solves the same-stem RAW (linear demosaic, camera white balance, no
auto-brightness, half size, channel mean), `auto` uses the RAW when a twin exists. Results always stay in the
JPG pixel space: the RAW is cropped to the JPG frame first. For the Sony A7R IV the decoded area (9568 x 6376)
is cropped to the JPG (9504 x 6336) at the centre (32 px left, 20 px top); that offset was measured by solving
both and agrees to 0.2 px. When a RAW does not map onto its JPG with certainty (different size and no matching
camera crop) the JPG is solved instead and a warning is logged. A lone RAW is always decoded. Measured on
DSC07625 (A7R IV, 14 mm): JPG 1889 matched stars, rms 0.34 px, grade A; RAW 481 stars, rms 0.34 px, grade A;
same 77 objects (41 at >= 65), Jupiter within 0.2 px. RAW is slower (about 26 s vs 20 s) and not better here.

## All options

One table for the engine and `astro-id.json`. Precedence: **command-line flag > nearest `astro-id.json` > default**.
The Facet sidecar passes a photo's own GPS (`--site`) and a date corrected in Facet (`--time`), which override the
file's site and offset.

| Option | Flag | `astro-id.json` | Default |
|---|---|---|---|
| Report cut | `--min-confidence` | - | 65 |
| Camera clock offset | `--offset` | `clock_offset` | none |
| True time of one frame | `--time`, `--timezone` | - | EXIF |
| Time tolerance | `--time-tolerance` | `time_tolerance_min` | 15 min |
| Timezone | `--timezone` (also for EXIF + offset, v1.1) | `timezone` | inferred from the site, else system |
| Site | `--site lat,lon[,elev]` | `site` | none (never assumed) |
| Results file | `--no-results-file` | - | on (`astro-id-results.json`) |
| Overlay folder | `--overlay-dir` | - | `<folder>-annotated` |
| Overlay scores | `--overlay-scores` | - | off |
| Skip overlay JPG | `--no-overlay` | - | off |
| Astrometry.net fallback | `--no-fallback` | - | on (needs key) |
| Solver pixels | `--solve-source jpg\|raw\|auto` | `solve_source` | `jpg` |
| Tag levels | `--tag-levels` | `tag_policy.levels` | 1,2,3,4 |
| Star tag limit | `--star-mag-limit` | `tag_policy.star_mag_limit` | off |
| Catalog tag limit | `--max-catalog-mag` | `tag_policy.max_catalog_mag` | 6.0 |
| Tag spelling | `--tag-style` | `tag_policy.tag_style` | `plain` |
| Stacking mode | `--stack-mode` | `stack_mode` | `representative` |
| Stacking gap | `--stack-gap` | `stack_gap_s` | 120 s |
| Stacking tolerance | - | `stack_tolerance` | 0.02 |
| Cull rule | `--cull-rule` (session) | `cull` | built-in (CONTRACT.md) |
| Quality metrics | `--no-quality` | - | on |

## Facet sidecar (`astro-id facet ...`)

astro-id runs beside Facet as its own process and uses only Facet's HTTP API, with its own credential. Nothing is
installed into Facet and Facet's database is not opened (one temporary exception for annotations, below).

```powershell
$env:FACET_URL = "http://127.0.0.1:5000"           # your Facet viewer
$env:FACET_EDITION_PASSWORD = "<edition password>" # or FACET_USERNAME + FACET_PASSWORD (multi-user), or FACET_TOKEN
uv run astro-id facet sync --path-prefix "D:\Photos\2026\trip\astro" --dry-run   # what would be written
uv run astro-id facet sync --path-prefix "D:\Photos\2026\trip\astro"             # solve + write
uv run astro-id facet sync --since last                                            # poll: new astro photos only
uv run astro-id facet serve                                                        # the session web UI
uv run astro-id facet status                                                       # what astro-id wrote where
uv run astro-id facet undo --path-prefix "D:\Photos\2026\trip\astro"             # remove astro-id's tags
```

- **Which photos:** `GET /api/photos` with `--category` (default `astro`; `''` = any), `--path-prefix`, `--tag`,
  `--album`, every frame of a burst included. Photos the ledger already holds are skipped unless `--force`.
  `--since YYYY-MM-DD` narrows by Facet's capture date; `--since last` uses the newest capture date the previous
  sync saw. Facet's API exposes no scan time, so polling = "photos the ledger has not seen yet".
- **What is written:** tags through `POST /api/photos/batch_manual_tags` (`plate-solved` or `plate-solve failed`
  plus the taggable objects, under Facet's 50-tag cap, other tags counted first), stale astro-id tags removed with
  `DELETE /api/photo/manual_tags`, and the annotation payload (`ANNOTATIONS.md`). Facet's reject/cull flags are
  never written. A Facet without object tags stores the objects as ordinary tags; the meaning is the same.
- **Ledger:** `%LOCALAPPDATA%\astro-id\facet-ledger.sqlite` (Windows) or `$XDG_DATA_HOME/astro-id/` records per
  Facet URL and photo the tags astro-id added, the annotation hash and the Facet version. A re-run removes only
  those tags; a tag someone else put on the photo is never claimed or removed. Re-running on unchanged results
  writes nothing.
- **When the ledger is behind** (no record, a record from an older version or another machine): a save first takes
  back the rows astro-id wrote, so a save at a new confidence converges to exactly the object tags at that cut. A
  Facet whose `get_photo` says who wrote each tag (`manual_tag_sources`) has those rows adopted; on model D the plain
  rows with a name astro-id could have written are offered once to `remove_tags`, which removes only the add-on's
  own. An older Facet that says neither keeps every unattributed row (its `remove_tags` would delete any row with
  that text); the save reports them as `tags_unknown_owner`. Counts (`tags_added`, `tags_removed`,
  `old_tags_removed`) are what Facet holds after the save, not what was planned.
- **Annotations:** `PUT /api/photo/annotations` when Facet has it (feature-detected). On a local Facet stack that
  has the `photo_annotations` table but not the route yet, `--legacy-db <db> --facet-dir <facet checkout>` writes
  them with Facet's own `db/annotations.py` (TEMPORARY). Otherwise they are skipped with a warning.
- **Paths:** the sidecar opens the files at the paths Facet stores; `--path-map FACET_PREFIX=LOCAL_PREFIX` when it
  sees them elsewhere (Facet in Docker).
- **Optional notifier:** `facet_plugin/astro_notify.py.example` appends scored astro paths to a spool file for
  `sync --spool`; not needed.
- **Moving from the old in-process plugin:** `astro-id facet adopt --legacy-db <db>` (TEMPORARY, read-only) puts
  the tags it wrote (`created_by='astro-id'`) and its annotation rows into the ledger once, so the sidecar can
  replace or remove them. A photo the ledger already has keeps its record and gains only the rows it lacks
  (`merged` in the summary).

`astro-id facet serve` starts a page on `http://127.0.0.1:<port>/<secret>/` (loopback only, random secret per
start): pick photos (Facet path prefix / album / tag), check each photo's location (Facet GPS, the folder's
astro-id.json site, or none; set one by pasting `lat, lon` or a Google Maps URL, or open the photo in Facet), confirm
the time (camera time, corrected time, offset, tolerance, timezone, JPG/RAW source; optionally save them to the
folder's astro-id.json), plate solve with live progress, then cull: per-frame quality, keep/review/reject with
reasons, an editable rule and re-cull without re-solving. Tagging review/reject frames `astro-review` is optional
and off by default.

## Facet add-on (sidebar inside Facet)

With a Facet that supports add-ons (the add-on boundary spec, `PLUGIN_BOUNDARY.md` v0.2), the same session runs in a
sidebar inside Facet. Facet starts and stops the astro-id process itself; you never run a command, set an env var
or type a password after the one-time install.

**Install (once):**

```powershell
# Windows (PowerShell)
cd <path to astro-id>
uv run astro-id facet install --facet-data "$env:LOCALAPPDATA\Facet\data"
```

```sh
# Linux / macOS
cd <path to astro-id>
uv run astro-id facet install --facet-data /path/to/facet/data
```

`--older-facet` leaves the manifest's `network` key out, for an older Facet that refuses keys it does not know.
This writes `<facet data>\addons\astro-id\addon.json` and nothing else. The manifest holds the absolute path of
`uv` (from `$env:UV`, else `uv` on PATH; `--uv` overrides) and of this checkout (`--project` overrides), so Facet
can start `uv run --project <checkout> astro-id facet serve --addon` without a PATH lookup. Re-run it after moving
the checkout or uv. Then, in Facet's `scoring_config.json`, the operator switch `"addons": {"enabled": true}` must
be on (Facet's setting, off by default; astro-id never edits Facet's config). Facet lists "Astro" on its Add-ons
page after a restart or a config reload.

**Uninstall:** `uv run astro-id facet uninstall --facet-data "$env:LOCALAPPDATA\Facet\data"` (deletes the
manifest and its folder). The ledger and astro-id's tags stay; `astro-id facet undo` removes the tags.

**Use:**

- **After an import:** in Facet's import dialog choose "After import, open: Astro". When the scan finishes, Facet
  offers "Open Astro for N photos"; the sidebar opens on exactly the photos that run saved (step 1 is filled in,
  filtered to category `astro` by default; clear the category box and Reload for every photo of the run).
- **By hand:** open Astro from the Add-ons page or the sidebar; it works on the current view, or on the selected
  photos (up to 500). Step 1 also keeps the manual folder / album / tag loader.
- **The steps** collapse as you go (location, time ± tolerance and RAW/JPG, plate solve, results and Save to
  Facet, sky labels, export, culling) so they fit the narrow sidebar; hover a row for detail. A requirements line
  under the title names anything missing (the same checks as `astro-id check`). "In Facet" links and the "Show keep / review / reject" buttons ask
  Facet to open or show those photos. After every write, Facet refreshes the photos that changed.
- **What it writes:** object tags and status tags (with astro-id's ledger; an older Facet without object tags
  stores them as ordinary tags and the sidebar says so), the sky labels if Facet has `PUT /api/photo/annotations` (else a
  note in the sidebar says annotations are skipped; there is no `--legacy-db` in add-on mode), and GPS through
  `PUT /api/photo/gps`. Never Facet's cull or reject flags. **Undo what Astro saved…** (step 4) removes
  astro-id's object tags, status tags and sky labels from the ticked or all loaded photos after a dry run and one
  inline confirmation; your own tags and old tags written outside the add-on stay.
- **Sky labels on this photo: Off/On** (step 5) shows or hides the labels while the panel is open (it starts Off);
  **Only names at confidence ≥** filters what is shown and exported, never what is saved.
- **Updates:** the add-on restarts itself on new code (Facet never notices), keeping the session. It tries the new
  version first: once the `.py` files have been unchanged for a few seconds, a throwaway probe starts them on a
  private port and must answer; only then does the panel switch. If the probe fails, the add-on keeps running the
  previous version and the panel says "An update to Astro is not ready yet; still running the previous version."
  until the files change again. Details: `astro_id/facet/keeper.py` and [the manual](MANUAL.md#9-updating).

**How it is wired (for the curious):** Facet passes `FACET_URL`, `FACET_TOKEN` (a per-launch add-on token),
`FACET_ADDON_ID` and `FACET_LAUNCH_SECRET`. astro-id binds `127.0.0.1` on a free port, prints
`FACET_ADDON_PORT=<n>` (the only line it ever writes to stdout), answers Facet's health challenge with
`HMAC-SHA256(secret, c)` and refuses every other request without `X-Facet-Launch: <secret>`. The page lives at
`/ui/` behind Facet's proxy (`/addons/astro-id/`), in a null-origin sandboxed iframe: no cookies or storage, the
per-open nonce in memory only, thumbnails fetched by astro-id's backend (never straight from Facet). It exits by
itself when Facet stops it or Facet exits. Details: `CONTRACT.md`, section "Facet add-on".

`astro-id facet serve` without `--addon` (the standalone page above) still works and is the way to use astro-id
with a Facet that has no add-on support.

## How it solves

1. The JPG is converted to grayscale, at half size when its long edge is 6000 px or more (a 60 MP frame),
   else at its own size: a downscaled copy already has 1-2 px stars, and halving it again would lose the
   faint ones. Stars are detected at several smoothing scales (sharp stars and defocused discs both work).
2. Wide fields (> 35 deg tall): a central ~30 deg crop is binned to ~2 arcmin/px (never finer than the
   image itself) and solved blind by ASTAP with W08 and the field size from EXIF. Small or coarse images
   (coarser than 100"/px, e.g. a 20 mm frame under ~4000 px) then try the same crop at their own scale
   and smoothly upsampled 2x, both with a looser ASTAP quad tolerance (`-t 0.014`). If that fails,
   larger/offset crops are tried. Narrow fields are solved whole.
3. The crop solution is grown to the whole frame: catalog stars are matched outward from the center
   and a TAN-SIP model (up to 4th order) is fitted, with outlier clipping each round, so the corners of
   a 20 mm frame are usable. Projections where the polynomial cannot be trusted are discarded.
4. Grade: **A** = 100+ matched stars and RMS <= 1 px; **B** = 30+ stars and RMS <= 2 px; **C** otherwise
   (RMS in full-resolution pixels).

**Small photos.** Downscaled copies solve from about **2400 px** on the long edge (measured on 20 mm
full-frame night and twilight frames: all solve at grade A from 2400 px and keep ~92 % of the full-size
high-confidence objects; dark-sky frames often solve down to 1600-2000 px, but small clusters and nebulae
drop out). A failed solve below 2400 px says so: "too few stars at this size ...; use a larger copy".

## Confidence scale

`confidence = 100 x solve quality x position certainty x detectability x margin` (integer, capped at 100)

| Factor | What it measures |
|---|---|
| Solve quality | grade A 1.00, B 0.92, C 0.75 |
| Position certainty | fit residual of the nearest matched stars at that spot (inflated when extrapolating) versus the object's size: `exp(-0.5 (err/size)^2)` |
| Detectability | object magnitude versus the frame's limiting magnitude (the faintest 0.25-mag bin where half the catalog stars are detected), logistic with 0.6 mag softness. Extended objects are dimmed by 2.5 mag per decade of size beyond the star image plus 1.5 mag. A star the fit actually matched scores 1. |
| Margin | partly out of frame (down to 0.4) and worst-case altitude within 3 deg of the horizon (0.6 at 0 deg, 0.4 below; only with a confirmed time and site that the photo agrees with, never for a star the fit matched) |
| Planets / Moon | x1.1 bonus when a bright source sits at the predicted spot; halved detectability when none does |
| Constellations | in frame when 2+ stick-figure stars are; confidence = solve quality x share of all figure stars detected |

| Tier | Confidence |
|---|---|
| high | >= 80 |
| likely | 65-79 (default `--min-confidence`) |
| candidate | < 65 |

All objects are listed in the JSON and CSV whatever their tier; consumers apply `--min-confidence`.
The overlay colors labels green (high), amber (likely) and gray (candidate); faint NGC/IC candidates
are left off the picture to keep it readable.

## Tag hierarchy and tag policy

A 20 mm frame holds dozens of named stars; tagging all of them buries the interesting objects. Each
object therefore carries a `level`, its `magnitude`, the constellation it lies in (`parent`), the group
that subsumes it (`member_of`) and a `taggable` flag computed from the tag policy:

| Level | What | Tagged by default |
|---|---|---|
| 1 | constellation | yes |
| 2 | Sun, Moon, planet | yes |
| 3 | Messier / NGC / IC object | yes, if magnitude <= `max_catalog_mag` (6.0) |
| 4 | asterism (Orion's Belt, Big Dipper, Summer Triangle, Winter Hexagon, Sickle of Leo, Teapot, W of Cassiopeia, ...) | yes |
| 5 | named star | no; `--star-mag-limit 1.5` tags the ~20 brightest |

- Members are never tagged next to their group: stars inside a tagged cluster's radius (the Pleiades
  stars under `m45 pleiades`) and asterism stars (`orion's belt`, not Alnitak/Alnilam/Mintaka).
- An asterism is found when at least 80 % of its member stars are in frame and detected; its
  confidence is solve quality x that share x horizon margin.
- `--tag-style kind` spells tags `constellation/orion`, `planet/jupiter`, `messier/m45 pleiades`,
  `asterism/orion's belt`, `star/sirius`.
- Per folder: `"tag_policy": {"levels": [1,2,3,4], "star_mag_limit": null, "max_catalog_mag": 6.0, "tag_style": "plain"}`
  in `astro-id.json`; flags override it.
- The JSON, CSV and overlay still list every object. On the overlay asterisms get pink stick figures and
  subsumed member stars a small unlabeled mark.

Asterisms are defined in `astro_id/tagpolicy.py` as lists of Hipparcos ids, written for this project.

## Stacking sequences

Astro shooting comes in bursts. In a folder run, frames taken back to back (gap <= 120 s, same lens,
focal length, exposure and size) form a sequence. The middle frame is plate-solved; every other frame is
registered to it by its star pattern (seconds, no ASTAP). If its pointing moved less than 2 % of the frame
diagonal it reuses the representative's objects, moved into its own pixel frame, and is reported as
`solver: "stack-copy"` with `stack_of`; otherwise it is solved on its own. Soft-focus or otherwise
"bad" frames are not skipped: a frame that registers gets the tags, one that does not is solved alone.
`--stack-mode all` (or `"stack_mode": "all"` in `astro-id.json`) solves every frame; `--stack-gap`
changes the 120 s window.

## Moon features (`astro-id moon`)

A photo of the Moon has no stars to solve, so the Moon is read from its own face. First version (plan, evidence and
what is left: [PLAN-moon.md](PLAN-moon.md)).

```powershell
uv run astro-id moon <photo-or-folder> [--time ISO] [--site lat,lon[,elev_m]] [--timezone ZONE]
                     [--north-angle DEG] [--mirror] [--no-fit] [--to DIR] [--no-overlay] [--overlay-scores]
                     [--tag-style plain|kind] [--json]
```

1. **Disc.** Thresholds from the dim (eclipsed) level up to the sunlit level; a RANSAC circle on each blob's edge
   that penalises blob pixels outside it (so the limb wins over a terminator or shadow edge); then the circle is
   refitted to the points where each ray falls half way from the disc to the sky. Quality from the arc coverage
   and the RMS.
2. **State.** Sunlit = above 22 % of the bright level (the maria are 30-50 % of the highlands). A phase always lights
   exactly half of the limb (all of it at full Moon); a lit limb under 140 deg or between 230 and 320 deg is the
   Earth's shadow. A dim, red unlit part (above 3 % of the lit level, R/G > 1.15) is the umbra; a whole disc with
   R/G > 1.5 is totality. With `--time`, a partial disc when the Sun lights all of it is an eclipse too. States:
   `full`, `gibbous`, `half`, `crescent`, `eclipse-partial`, `eclipse-total`.
3. **Orientation** (where the Moon's north pole points on the picture), first that applies: `--north-angle` plus the
   axis angle for the time (certainty 1.0; 0.6 without a time); time + site with the camera roll assumed level
   (image up = zenith, celestial north at minus the parallactic angle; certainty 0.75, or 0.5 with a raw EXIF time
   that no `clock_offset` or `--time` confirms); an albedo fit (a crude
   map of the maria correlated with the disc, the lighting flattened region by region, 2-deg steps; certainty from
   the peak's margin over the best angle more than 30 deg away, at most 0.9; not tried under 40 px). On NASA SVS
   frames turned and shrunk to 65-650 px, the fit was right within 5 deg in all 50 full-Moon cases (25 with a
   synthetic eclipse shadow) and in 24 of 25 gibbous ones (the other was 9 deg off).
4. **Libration and axis** (Meeus, *Astronomical Algorithms*, ch. 53, physical libration ignored): from the Moon's
   apparent position (DE421). Agrees with NASA SVS within 0.03 deg (libration) and 0.15 deg (axis angle).
5. **Features:** a curated 92 (28 maria and bays, 48 craters, 10 mountain ranges, 6 Apollo sites) from the IAU/USGS
   Gazetteer of Planetary Nomenclature, projected orthographically. A feature is named when it is at least 6 px on
   the image (Apollo sites count as 40 km) and on the visible part of the disc.
6. **Confidence** = `100 x disc quality x orientation certainty x size factor x illumination`; size factor
   `1 - exp(-(size/6 px)^2)`; illumination = the sunlit share of the spot, a spot in a visible umbra at 0.6.
   Names under 20 stay in the JSON but are not drawn.

Output: `<name>_moon.jpg` (next to the photo or in `--to`), a summary line per photo on stderr, and with `--json`
one document `{"contract": 1, "moon": [...]}`: per photo `found`, `message`, `disc` (`cx`, `cy`, `r`,
`diameter_px`, `rms_px`, `coverage`, `quality`), `state`, `orientation` (`method`, `certainty`, `pole_angle_deg`,
`mirrored`, ...), `ephemeris` (sub-Earth point, axis and parallactic angles) when a time is known, `features`
(name, kind, lat, lon, x, y, size_px, confidence, tag) and `annotations` (the [overlay payload](#annotations-payload-for-the-facet-viewer)
with one group, `moon`). Exit 0 when a disc was found in at least one photo, 1 when none, 2 for a usage error.
Not yet in the Facet add-on.

## Optional fallback: astrometry.net

Only when ASTAP fails **and** the environment variable `ASTROMETRY_API_KEY` is set, a downscaled
grayscale JPG is uploaded to nova.astrometry.net as a private submission
(`publicly_visible: n`, `allow_commercial_use: n`). The key is read from the environment only and
never written anywhere. `--no-fallback` disables it.

