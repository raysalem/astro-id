# astro-id engine ↔ Facet plugin contract (v1)

Fixed before the engine and the plugin are built in parallel. Change it only with a version bump and both sides updated.

## Engine CLI (`uv run --project <repo> astro-id ...`)

```
astro-id solve <file-or-folder> [--min-confidence 65] [--offset +2559d11h08m]
               [--time ISO8601 [--timezone IANA]]
               [--time-tolerance 15m] [--site lat,lon[,elev_m]]
               [--overlay-dir DIR] [--json] [--no-overlay] [--no-fallback]
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
