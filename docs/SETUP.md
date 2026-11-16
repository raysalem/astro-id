# astro-id: setup

## Install (users)
1. Install Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/), and the ASTAP command-line solver with the W08 star database ([REQUIREMENTS](REQUIREMENTS.md)).
2. Clone this repo, then `uv sync`.
3. `uv run astro-id check` verifies the setup.

## Develop
1. Clone, then `uv sync --frozen`.
2. Run the suites in [TESTING](TESTING.md).
3. Work in a branch or worktree, never in a checkout a running Facet add-on uses: the add-on restarts itself when its files change.
4. Check: `python dev-notes/tools/check.py` prints `0 error(s)`.

## Configuration
| Key / file | Default | Meaning |
|---|---|---|
| `astro-id.json` (per folder or album) | none | site, clock offset, tolerance, source (JPG/RAW), export options; see [REFERENCE](REFERENCE.md) |
| Facet add-on manifest | written by `astro-id facet install` | one `addon.json` under Facet's data folder |

## Environment variables
| Name | Used for | Required |
|---|---|---|
| `ASTAP_PATH`, `ASTAP_DB` | where the solver and its star database are | no (PATH and default folders are searched) |
| `FACET_URL`, `FACET_TOKEN`, `FACET_ADDON_ID`, `FACET_LAUNCH_SECRET` | set by Facet when it starts the add-on | add-on only |
| `FACET_EDITION_PASSWORD`, `FACET_USERNAME`, `FACET_PASSWORD` | stand-alone `astro-id facet sync/serve` credentials | stand-alone Facet use only |
| `FACET_DIR` | a Facet checkout for the `facet_contract` tests | no (tests skip without it) |

Names only, never values.
