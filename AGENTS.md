# AGENTS.md

<!-- dev-notes:begin v1.1.0 -->
## Dev notes (plan before edits)
This repo follows dev-notes v1.1.0 (dev-notes/STANDARD.md).
1. Read this file, the open plans in docs/plans/, and the Decisions and Gotchas in docs/DETAILS.md.
2. Look at the code your task touches (language server or text search). Code is the truth: where a doc disagrees with it, fix the doc.
3. Write your plan to docs/plans/ (one file per task, named by date and slug) before you edit any code. Leave other open plans alone.
4. A plan that changes design (a Decision in docs/DETAILS.md) waits for the owner's OK.
5. In the same change: record new decisions and gotchas in docs/DETAILS.md and bugs in docs/ISSUES.md, set the plan's status, run `python dev-notes/tools/check.py`.
6. Do not push to shared branches, merge, or open PRs unless the owner says so.
<!-- dev-notes:end -->

## Brief
astro-id plate-solves night-sky photos and names what is in them: constellations, Messier/NGC/IC objects, named stars, Sun, Moon and planets, each with a 0-100 confidence.
It is for photographers who shoot the sky on a tripod and want to know what they caught, with or without the Facet photo library.
It runs ASTAP on each photo, refits the frame, and identifies objects against reference catalogs. It is not a telescope controller, a stacker or a star-chart app.

| Part | Folder | Job |
|---|---|---|
| Engine | `astro_id/` | solve, identify, score, quality, cull, labeled copies |
| Facet add-on | `astro_id/facet/` | panel server, sync over Facet's MCP/HTTP API, self-updating keeper |
| Panel page | `astro_id/facet/static/` | the sandboxed Astro panel (HTML + bridge) |
| Facet plugin notes | `facet_plugin/` | the removed in-process plugin, kept as a notifier example |
| Tools | `tools/` | docs check, example and demo builders |
| Tests | `tests/` | Python suites, Node panel/bridge tests, live scripts |
| Examples | `examples/` | five example frames with expected results |

Docs: decisions and gotchas in `docs/DETAILS.md`; bugs in `docs/ISSUES.md`; plans in `docs/plans/`; users start at `docs/USAGE.md`; interfaces in `CONTRACT.md` and `ANNOTATIONS.md`.

## Commands
| Do | Command |
|---|---|
| Test (Python) | `uv run pytest -q -rs` |
| Test (panel, Node) | `node --test tests/js/` |
| Docs link check | `uv run python tools/check_docs.py` |
| dev-notes check | `python dev-notes/tools/check.py` |

## Repo-specific rules
- The Facet add-on restarts itself when files in a checkout it runs from change: do all work in a branch or worktree, never in the checkout the owner runs.
- Keep this repo public-safe: no personal paths, private addresses, real library paths or names in docs or tests.
