# Requirements

The minimums live in one place in the code: `astro_id/requirements.py`. This page repeats them.

| What | Minimum | Why / how it is detected |
|---|---|---|
| Python | 3.12 | `requires-python` in `pyproject.toml` |
| uv | any recent | starts the Facet add-on (`uv run --project ... astro-id facet serve --addon`); found on PATH |
| ASTAP command-line solver | **2026.10.30** | The release astro-id was built and tested with. ASTAP versions are dates (`YYYY.MM.DD`). Read from `astap_cli -h`, which prints `ASTAP astrometric solver version CLI-2026.10.30`. The flags astro-id uses (`-f -fov -r -d -D -z`) are long-standing, but no older release has been run, so the oldest known-good one is the floor. Download: https://www.hnsky.org/astap.htm |
| ASTAP star database W08 | required | Wide field (fov 20 degrees and up). Present when a `w08_0101.*` file sits in the ASTAP folder or in `$ASTAP_DB` |
| ASTAP star database D50 | optional | Narrow field (under 20 degrees); D20, D05, V50, H18 or G05 also work. Detected the same way (`d50_0101.*`) |
| Writable temp folder | yes | solves write a small FITS and result file per attempt |
| Internet | first run only | reference catalogs are downloaded once; `--offline` skips this check |
| Facet add-on API (`facet_api` in the add-on manifest) | **1** | Facet reports it in its `status` answer to an add-on token. Older Facets report nothing |

## What Facet features do

astro-id reads Facet's `status` and tool list once (no probing writes) and picks a mode:

| Facet offers | astro-id does |
|---|---|
| annotations + plugin tags + batch annotations (model D) | names become searchable **object tags**; sky labels are drawn on the photo |
| tags, annotation write, but not model D | plain per-object tags; sky labels still written |
| tags only | plain tags, **no sky labels** |
| nothing writable | the panel shows a one-line note; check the add-on's permissions |

## `astro-id check`

```
astro-id check [--facet URL] [--offline] [--json]      (alias: astro-id doctor)
```

One line per requirement: a check mark = fine, a cross = required and failing (with what to do), `!` = optional or
unknown. Exit code 0 when every required check passes, 1 otherwise. `--json` prints
`{"ok": bool, "checks": [{"key", "state": ok|fail|warn, "text", "fix"}], "minimums": {...}}`.

`--facet URL` also checks that Facet: reachable, its version, `facet_api` against the minimum, and which mode astro-id
will use (object tags, plain tags, no sky labels). Set `FACET_TOKEN` to an add-on token so Facet reports `facet_api`.
Under the add-on environment (`FACET_ADDON_ID` and `FACET_URL` set) it checks that Facet without the flag.

## What happens below a minimum

- `astro-id solve` and `astro-id session` stop before doing any work with one sentence and exit 2, for example
  `ASTAP 2024.5.1 is older than the minimum 2026.10.30; update it from https://www.hnsky.org/astap.htm. Run astro-id check for details.`
  A missing ASTAP stops too, unless an `ASTROMETRY_API_KEY` is set and `--no-fallback` is not (then only the
  astrometry.net fallback can solve, with a warning).
- The Facet add-on keeps running (so its panel can say what is wrong), logs each problem, and reports
  `requirements: {ok, problems}` in `api/state`; the panel shows the problems as one calm line under the header.
  Starting a solve from it stops with the same sentence.
