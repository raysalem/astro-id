# Plan: adopt the dev-notes standard

Goal: bring this repo's docs in line with dev-notes (adopted at v1.0.0, moved to v1.1.0 the same day). Branch: `adopt-dev-notes`. Started 2026-11-15. Status: done 2026-11-15 (merged into main by fast-forward on the owner's word).

## Big picture
1. Project: astro-id, a plate-solving and sky-labeling tool and Facet add-on.
2. Task: docs only. Vendor `dev-notes/`, add AGENTS.md, CLAUDE.md, DETAILS, ISSUES, plans, folder READMEs, the CI check.
3. Touches: `docs/`, folder READMEs, `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `.github/workflows/dev-notes.yml`, `dev-notes/`. No code.
4. Nearby: [TODO](../TODO.md) "Docs"; the branches `label-placement` and `twilight-cull` were open at the same time and touch code, not these files.
5. Risk: the live add-on runs from the main checkout; this work happens in a worktree. The `dev-notes/` name was an ignored private link in the main checkout: renamed to `dev-notes-private/` before the merge.

## Steps
| # | Step | Files / symbols | Status |
|---|---|---|---|
| 1 | `adopt.py init` | `dev-notes/`, AGENTS.md, CLAUDE.md, docs/*, workflow | done 2026-11-15 |
| 2 | Move private notes into public docs, scrubbed | docs/BRIEF DETAILS FEATURES USAGE SETUP TODO ISSUES, docs/archive/ | done 2026-11-15 |
| 3 | Folder READMEs | `astro_id/`, `astro_id/facet/`, `tests/`, `tests/js/`, `tests/live/`, `tools/`, `examples/` | done 2026-11-15 |
| 4 | Move to dev-notes v1.1.0: `adopt.py update`; this plan to `docs/plans/`; BRIEF folded into AGENTS.md and archived; new workflow file | `dev-notes/`, `AGENTS.md`, `docs/plans/`, `docs/archive/BRIEF-2026-11.md`, `.github/workflows/dev-notes.yml` | done 2026-11-15 |
| 5 | Merge `origin/main` (Moon release 8c8ee5c): no conflicts | whole tree | done 2026-11-15 |
| 6 | Bring the notes up to the current code: `astro-id moon`, `label`, `check`, the keeper, the panel's Undo, Sky labels switch, requirements line and clock warning; folder READMEs to the v1.1.0 shape (no name tables) | AGENTS.md, DETAILS, FEATURES, USAGE, TODO, folder READMEs, `examples/README.md` | done 2026-11-15 |
| 7 | Moon plan into `docs/plans/2026-11-15-moon-features.md`; `docs/PLAN-moon.md` kept as a pointer (code cites it) | `docs/PLAN-moon.md`, `docs/plans/` | done 2026-11-15 |
| 8 | `.gitignore`: vendored `dev-notes/` tracked, `dev-notes-private/` ignored | `.gitignore` | done 2026-11-15 |
| 9 | Scrub the moved notes: real photo file names, the local Facet checkout name, trip folder name, NAS and private-notes paths | DETAILS, ISSUES, FEATURES, USAGE, `docs/archive/` | done 2026-11-15 |
| 10 | Checks, tests, CI | `dev-notes/tools/check.py`, `tools/check_docs.py`, pytest, node, `ci.yml`, `dev-notes.yml` | done 2026-11-15 |

Navigation: text search (no language server on this machine).

## Decisions
- Private notes that are not public-safe (test sheets with real paths, merge-request drafts, reviews) stay in the maintainer's private notes folder.
- Existing hand-written docs (MANUAL, SIMPLE, REFERENCE, REQUIREMENTS, TESTING, CONTRACT, ANNOTATIONS) are kept as they are.
- Folder READMEs keep a Brief and a Tests section but no table of module names (v1.1.0: names are read from the code).

## Open for the next dev
- Repoint `astro_id/moon.py` (docstring and one user message) and `tests/test_moon.py` to `docs/plans/2026-11-15-moon-features.md`, then archive the `docs/PLAN-moon.md` pointer.
- `CONTRACT.md` and `docs/REFERENCE.md` (not moved notes, so not scrubbed here) still use real camera file names as examples; replace them when those docs are next touched.
- Decide whether `tools/check_docs.py` stays beside `dev-notes/tools/check.py` ([TODO](../TODO.md) "Docs").
- DETAILS lacks model D, the keeper and safe update as design sections (they are in FEATURES).
- Size warnings: `CONTRACT.md`, `docs/MANUAL.md`, `docs/REFERENCE.md` are over 400 lines.
