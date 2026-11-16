# Plan: adopt the dev-notes standard

Goal: bring this repo's docs in line with dev-notes (adopted at v1.0.0, moved to v1.1.0 the same day). Branch: `adopt-dev-notes`. Started 2026-11-15. Status: in progress (not merged, not pushed).

## Big picture
1. Project: astro-id, a plate-solving and sky-labeling tool and Facet add-on.
2. Task: docs only. Vendor `dev-notes/`, add AGENTS.md, CLAUDE.md, the ten `docs/` files, folder READMEs, CI check.
3. Touches: `docs/`, folder READMEs, `.gitignore`, `.github/workflows/dev-notes.yml`. No code.
4. Nearby: [TODO](../TODO.md) "Docs".
5. Risk: the live add-on runs from the main checkout; this work happens in a worktree. The `dev-notes/` name was an ignored private junction in the main checkout: rename it before merging (see below).

## Steps
| # | Step | Files / symbols | Status |
|---|---|---|---|
| 1 | `adopt.py init` | `dev-notes/`, AGENTS.md, CLAUDE.md, docs/*, workflow | done 2026-11-15 |
| 2 | Move private notes into public docs, scrubbed | docs/BRIEF DETAILS FEATURES USAGE SETUP TODO ISSUES, docs/archive/ | done 2026-11-15 |
| 3 | Folder READMEs | `astro_id/`, `astro_id/facet/`, `tests/`, `tests/js/`, `tests/live/`, `tools/`, `examples/` | done 2026-11-15 |
| 4 | Check to green | `python dev-notes/tools/check.py` | see TESTING |
| 5 | Move to dev-notes v1.1.0: `adopt.py update`; this plan moved to `docs/plans/`; BRIEF folded into AGENTS.md and archived; new workflow file | `dev-notes/`, `AGENTS.md`, `docs/plans/`, `docs/archive/BRIEF-2026-11.md`, `.github/workflows/dev-notes.yml` | done 2026-11-15 |

Navigation: text search fallback (no language server); code was not opened.

## Decisions
- Private notes that are not public-safe (test sheets with real paths, merge-request drafts, reviews) stay in the maintainer's private notes folder.
- Existing hand-written docs (MANUAL, SIMPLE, REFERENCE, REQUIREMENTS, TESTING) are kept as they are.

## Open for the next dev
- v1.1.0 makes FEATURES, USAGE, SETUP, REQUIREMENTS, TESTING, TODO and the folder READMEs optional. They are kept here; drop a folder README's name table when it next goes stale.
- Before merging into `main`: the main checkout has an ignored folder named `dev-notes/` that is a link to private notes. Rename it to `dev-notes-private/` first, or the merge cannot create the vendored `dev-notes/`.
- Decide whether `tools/check_docs.py` stays beside `dev-notes/tools/check.py`.
- DETAILS lacks model D, the keeper and safe update as design sections (they are in FEATURES).
