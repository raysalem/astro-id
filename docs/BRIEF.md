# astro-id: brief

astro-id plate-solves night-sky photos and names what is in them: constellations, Messier/NGC/IC objects, named stars, Sun, Moon and planets, each with a 0-100 confidence.
It is for photographers who shoot the sky on a tripod and want to know what they caught, with or without the Facet photo library.
It runs ASTAP on each photo, refits the frame, and identifies objects against reference catalogs, with a worst-case time window for the Moon, planets and horizon.
It is not a telescope controller, a stacker or a star-chart app.

Status: v0.x. The engine, the CLI (`astro-id solve`, `label`) and the Facet add-on are built and tested; the Facet add-on panel is the main work area. Updated 2026-11-15.

## Main parts
| Part | Folder | Job |
|---|---|---|
| Engine | `astro_id/` | solve, identify, score, quality, cull, labeled copies |
| Facet add-on | `astro_id/facet/` | panel server, sync over Facet's MCP/HTTP API, self-updating keeper |
| Panel page | `astro_id/facet/static/` | the sandboxed Astro panel (HTML + bridge) |
| Facet plugin notes | `facet_plugin/` | the removed in-process plugin, kept as a notifier example |
| Tools | `tools/` | docs check, example and demo builders |
| Tests | `tests/` | Python suites, Node panel/bridge tests, live scripts |
| Examples | `examples/` | five example frames with expected results |

## Doc map
| Need | File |
|---|---|
| What it does | [FEATURES](FEATURES.md) |
| How to use it | [USAGE](USAGE.md) · [MANUAL](MANUAL.md) · [SIMPLE](SIMPLE.md) · [REFERENCE](REFERENCE.md) |
| Install / dev setup | [SETUP](SETUP.md) |
| What it needs | [REQUIREMENTS](REQUIREMENTS.md) |
| How it works | [DETAILS](DETAILS.md) |
| Tests | [TESTING](TESTING.md) |
| Current work | [PLAN](PLAN.md) · [TODO](TODO.md) · [ISSUES](ISSUES.md) |
| Interfaces | [CONTRACT](../CONTRACT.md) · [ANNOTATIONS](../ANNOTATIONS.md) |
| History | [archive/](archive/) |
