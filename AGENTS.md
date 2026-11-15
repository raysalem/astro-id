# AGENTS.md

<!-- dev-notes:begin v1.0.0 -->
## Dev notes (docs first)
This repo follows dev-notes v1.0.0 (dev-notes/STANDARD.md).
1. Read docs/BRIEF.md, then docs/PLAN.md, docs/TODO.md, and the README.md of each folder you will touch.
2. Docs first: write your plan into docs/PLAN.md before you open any code.
3. Open code only through the plan, with your language server (LSP) or a text search. Never browse code to get oriented.
4. If the docs for your area are missing or wrong, fix them first or stop and say so (STANDARD.md 1.1).
5. Update every doc your change touches in the same change; run `python dev-notes/tools/check.py`.
6. Do not push to shared branches, merge, or open PRs unless the owner says so.
<!-- dev-notes:end -->

## Repo-specific rules
- Tests: `uv run pytest -q -rs`; `node --test tests/js/`. Docs link check: `uv run python tools/check_docs.py`. Drift check: `python dev-notes/tools/check.py`.
- The Facet add-on restarts itself when files in a checkout it runs from change: do all work in a branch or worktree, never in the checkout the owner runs.
- Keep this repo public-safe: no personal paths, private addresses, real library paths or names in docs or tests.
