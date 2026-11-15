# astro_id: the engine

## Brief
The plate-solving and identification library behind the `astro-id` command: it solves a photo with ASTAP, refits the whole frame, names what is in it, scores each object 0-100, measures frame quality and writes results. TODO(verify): one-line purposes below were taken from the dated notes in [DETAILS](../docs/DETAILS.md), not re-read from code.

Public surface:

| Name | Kind | Use |
|---|---|---|
| `cli.py` | command | the `astro-id` entry point (`solve`, `label`, `session`, `cull`, `check`, `site`, `facet ...`) |
| `solve.py` | module | ASTAP crop solve and full-frame TAN-SIP refit |
| `identify.py` | module | name constellations, deep-sky objects, stars, Sun, Moon, planets for a solved frame |
| `confidence.py` | module | 0-100 confidence and tiers, worst case across the clock tolerance |
| `quality.py` | module | per-frame quality metrics and grade |
| `cull.py` | module | keep / review / reject from the metrics |
| `session.py` | module | an astro session: time preview, solve with overrides, JSON-lines progress |
| `export.py` | module | labeled copies from a stored result |
| `label.py` | module | the simple `astro-id label` command |
| `overlay.py` | module | drawing the labels |
| `site.py`, `timeutil.py`, `config.py` | module | site, clock offset and per-folder `astro-id.json` |
| `tagpolicy.py`, `vocab.py` | module | which names become tags; the name vocabulary |
| `requirements.py` | module | minimum versions and the `astro-id check` command's rules |
| `facet/` | folder | the Facet add-on ([README](facet/README.md)) |

Depends on: ASTAP (external), astropy, numpy and the other packages in `pyproject.toml`. Used by: `astro_id/facet/`, the `astro-id` command, `tests/`.

## Tests
- Covered by: `tests/test_*.py` (solve, identify, quality, cull, session, site, horizon, small photos, labels).
- Run just these: `uv run pytest -q tests -k "not facet"`.
- Gaps: no real-sky run in CI beyond the example set (`tests/check_examples.py`).
