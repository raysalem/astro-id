# astro-id engine ↔ Facet plugin contract (v1)

Fixed before the engine and the plugin are built in parallel. Change it only with a version bump and both sides updated.

## Engine CLI (`uv run --project <repo> astro-id ...`)

```
astro-id solve <file-or-folder> [--min-confidence 65] [--offset +2559d11h08m]
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
  "time_source": "config" | "exif",
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
