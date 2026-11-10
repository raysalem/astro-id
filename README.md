# astro-id

Plate-solve night-sky photos and name what is in them: Messier and bright NGC/IC objects, IAU-named
bright stars, asterisms, the 88 constellations, the Sun, Moon and planets. Every object gets a **0-100 confidence**
and a tier, and each frame gets an annotated overlay JPG with constellation stick figures.

Works for wide-field (14-35 mm) and narrow-field shots. Built to run on its own or behind the
[Facet](https://github.com/ncoevoet/facet) photo-library plugin in `facet_plugin/`.

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
uv run astro-id site <folder>     # pick the observing site on Google Maps -> astro-id.json
uv run astro-id setup             # download data, check ASTAP
```

- A folder is scanned for JPG/JPEG/TIF/PNG and RAW. A RAW file is replaced by its same-stem JPG when
  there is one (RAW+JPG pairs count once); a lone RAW is decoded.
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
