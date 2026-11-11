# astro-id

Plate-solve night-sky photos and name what is in them: Messier and bright NGC/IC objects, IAU-named
bright stars, asterisms, the 88 constellations, the Sun, Moon and planets. Every object gets a **0-100 confidence**
and a tier, and each frame gets an annotated overlay JPG with constellation stick figures.

Works for wide-field (14-35 mm) and narrow-field shots. Runs on its own, or as a sidecar next to the
[Facet](https://github.com/ncoevoet/facet) photo library (`astro-id facet sync|serve`, over Facet's HTTP API).

## Install

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
git clone <this repo> astro-id
cd astro-id
uv sync
```

### ASTAP (plate solver) and its star database

astro-id uses the [ASTAP](https://www.hnsky.org/astap.htm) command-line solver (MPL-2.0, by Han Kleijn).

Windows, per user, no admin rights:

1. Download the command-line solver `astap_command-line_version_win64.zip` from the ASTAP page
   (SourceForge project `astap-program`, folder `windows_installer`) and unzip `astap_cli.exe` into
   `%LOCALAPPDATA%\astap\`.
2. Download the **W08** wide-field star database installer `w08_star_database_mag08_astap.exe`
   (SourceForge `astap-program`, folder `star_databases`) and install it silently into the same folder:
   ```powershell
   .\w08_star_database_mag08_astap.exe /VERYSILENT /SUPPRESSMSGBOXES /SP- /DIR="$env:LOCALAPPDATA\astap"
   ```
   W08 covers fields of roughly 20 degrees and up (wide-angle lenses). For telephoto frames
   (field under ~20 degrees, e.g. 85-200 mm on full frame) also install **D50** the same way.
3. Check: `uv run astro-id setup` downloads the reference data and reports the ASTAP path and databases.

astro-id finds ASTAP through the `ASTAP_PATH` environment variable (the exe or its folder), then
`%LOCALAPPDATA%\astap`, `%ProgramFiles%\astap`, and `PATH`. The star database is taken from
`ASTAP_DB` or the folder of the exe. Linux/macOS: install ASTAP from the same site and set `ASTAP_PATH`.

### Optional fallback: astrometry.net

Only when ASTAP fails **and** the environment variable `ASTROMETRY_API_KEY` is set, a downscaled
grayscale JPG is uploaded to nova.astrometry.net as a private submission
(`publicly_visible: n`, `allow_commercial_use: n`). The key is read from the environment only and
never written anywhere. `--no-fallback` disables it.

### Reference data (downloaded on first run, not in this repo)

Cached in `%LOCALAPPDATA%\astro-id\data` (override with `ASTRO_ID_DATA`).

| Data | Source | License |
|---|---|---|
| Hipparcos main catalog (I/239) | CDS / VizieR (CfA mirror as fallback) | ESA, free for use with acknowledgement |
| JPL DE421 ephemeris | via Skyfield loader | public domain (NASA/JPL) |
| OpenNGC (NGC.csv, addendum.csv) | github.com/mattiaverga/OpenNGC | CC-BY-SA 4.0 |
| IAU star names (WGSN, IAU-CSN.txt) | pas.rochester.edu/~emamajek/WGSN | IAU WGSN, cite the IAU |
| Constellation lines (`constellationship.fab`, modern sky culture, v23.4) | Stellarium | GPL-2.0-or-later |

None of these files are redistributed; the GPL and CC-BY-SA data stay outside the MIT-licensed code.

## Usage

```powershell
uv run astro-id solve <file-or-folder> [--min-confidence 65] [--offset +2559d11h08m]
                     [--time-tolerance 15m] [--site lat,lon[,elev_m]]
                     [--overlay-dir DIR] [--json] [--no-overlay] [--no-fallback] [-v]
                     [--tag-levels 1,2,3,4] [--star-mag-limit M] [--max-catalog-mag 6.0]
                     [--tag-style plain|kind] [--stack-mode representative|all] [--stack-gap 120]
                     [--solve-source jpg|raw|auto] [--overlay-scores]
uv run astro-id site <folder>     # pick the observing site on Google Maps -> astro-id.json
uv run astro-id setup             # download data, check ASTAP
```

- A folder is scanned for JPG/JPEG/TIF/PNG and RAW. A RAW file is replaced by its same-stem JPG when
  there is one (RAW+JPG pairs count once); a lone RAW is decoded.
- Overlay labels are plain names ("Uranus"); `--overlay-scores` adds the confidence ("Uranus 100"). The legend keeps
  the tier colour key. The live, filterable overlay is drawn by the Facet viewer from the `annotations` payload
  (see below); the JPG is the static copy.
- Overlays (`<stem>_astro.jpg`, 3000 px wide) and `astro-id-report.csv` go to `--overlay-dir`, default
  the sibling folder `<folder>-annotated`. Originals are never written to.
- `--json` prints one document `{"contract": 1, "results": [...]}` to stdout (see `CONTRACT.md`);
  logs go to stderr. Exit code is 0 when the run completed, even if some frames did not solve.

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

`astro-id site <folder>` opens Google Maps in Chrome at the folder's site, asks you to paste
`lat, lon` (right-click the spot in Maps and click the first line to copy it) or a Maps URL, looks up
the elevation from USGS EPQS (US only; you are asked otherwise), asks for a name, confirms, and
writes/merges `astro-id.json` (keeping any clock offset, tolerance and timezone already there).

### Time and tolerance

Camera clocks drift. Every time-dependent check (planet and Moon positions, above-horizon, horizon
margin) is evaluated across `[t - tolerance, t + tolerance]` and the **worst case** is scored. When the
Moon is found in the frame its position pins the time (it moves ~0.5 deg per hour against the stars):
`best_fit_time` is reported and `time_flag` is set if it falls outside the tolerance. Without an
`astro-id.json` or `--offset`, EXIF time is used as-is (`time_source: "exif"`) and the Sun, Moon and
planets are capped at *candidate*.

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
and measures the disc of a defocused one), trailing as the median elongation (major/minor axis), sky background
and noise, and an A/B/C quality grade. Stars are detected at three smoothing scales; the scale whose kernel matches
the stars is used, so a badly defocused frame is measured on whole discs, not fragments. `--no-quality` skips it
(about 5-15 s per 61 MP frame, mostly JPEG decoding).

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
| Site | `--site lat,lon[,elev]` | `site` | none |
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
  never written. Until Facet has plugin-owned tags, these are stored as ordinary user tags.
- **Ledger:** `%LOCALAPPDATA%\astro-id\facet-ledger.sqlite` (Windows) or `$XDG_DATA_HOME/astro-id/` records per
  Facet URL and photo the tags astro-id added, the annotation hash and the Facet version. A re-run removes only
  those tags; a tag someone else put on the photo is never claimed or removed. Re-running on unchanged results
  writes nothing.
- **Annotations:** `PUT /api/photo/annotations` when Facet has it (feature-detected). On a local Facet stack that
  has the `photo_annotations` table but not the route yet, `--legacy-db <db> --facet-dir <facet checkout>` writes
  them with Facet's own `db/annotations.py` (TEMPORARY). Otherwise they are skipped with a warning.
- **Paths:** the sidecar opens the files at the paths Facet stores; `--path-map FACET_PREFIX=LOCAL_PREFIX` when it
  sees them elsewhere (Facet in Docker).
- **Optional notifier:** `facet_plugin/astro_notify.py.example` appends scored astro paths to a spool file for
  `sync --spool`; not needed.
- **Moving from the old in-process plugin:** `astro-id facet adopt --legacy-db <db>` (TEMPORARY, read-only) puts
  the tags it wrote (`created_by='astro-id'`) and its annotation rows into the ledger once, so the sidecar can
  replace or remove them.

`astro-id facet serve` starts a page on `http://127.0.0.1:<port>/<secret>/` (loopback only, random secret per
start): pick photos (Facet path prefix / album / tag), check each photo's location (Facet GPS, the folder's
astro-id.json site, or none; set one by pasting `lat, lon` or a Google Maps URL, or open the photo in Facet), confirm
the time (camera time, corrected time, offset, tolerance, timezone, JPG/RAW source; optionally save them to the
folder's astro-id.json), plate solve with live progress, then cull: per-frame quality, keep/review/reject with
reasons, an editable rule and re-cull without re-solving. Tagging review/reject frames `astro-review` is optional
and off by default.

## How it solves

1. The JPG is read at half size and converted to grayscale. Stars are detected at several smoothing
   scales (sharp stars and defocused discs both work).
2. Wide fields (> 35 deg tall): a central ~30 deg crop is binned to ~2 arcmin/px and solved blind by
   ASTAP with W08 and the field size from EXIF. If that fails, larger/offset crops are tried.
   Narrow fields are solved whole.
3. The crop solution is grown to the whole frame: catalog stars are matched outward from the center
   and a TAN-SIP model (up to 4th order) is fitted, with outlier clipping each round, so the corners of
   a 20 mm frame are usable. Projections where the polynomial cannot be trusted are discarded.
4. Grade: **A** = 100+ matched stars and RMS <= 1 px; **B** = 30+ stars and RMS <= 2 px; **C** otherwise
   (RMS in full-resolution pixels).

## Confidence scale

`confidence = 100 x solve quality x position certainty x detectability x margin` (integer, capped at 100)

| Factor | What it measures |
|---|---|
| Solve quality | grade A 1.00, B 0.92, C 0.75 |
| Position certainty | fit residual of the nearest matched stars at that spot (inflated when extrapolating) versus the object's size: `exp(-0.5 (err/size)^2)` |
| Detectability | object magnitude versus the frame's limiting magnitude (the faintest 0.25-mag bin where half the catalog stars are detected), logistic with 0.6 mag softness. Extended objects are dimmed by 2.5 mag per decade of size beyond the star image plus 1.5 mag. A star the fit actually matched scores 1. |
| Margin | partly out of frame (down to 0.4) and worst-case altitude within 3 deg of the horizon (0.6 at 0 deg, 0.4 below) |
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

## Development

```powershell
uv run pytest -q
```

Tests use synthetic frames rendered from Hipparcos through a known WCS (no ASTAP or photos needed,
but the reference data is downloaded on first run).

## License

MIT, see `LICENSE`. Third-party data keeps its own license (table above).
