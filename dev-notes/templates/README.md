# templates: files copied into adopting repos

Placeholders are in angle brackets. A doc line left identical to a template placeholder line fails the check (D10). Keep each template's headings so agents can find sections.

| Name | Becomes | Written by |
|---|---|---|
| `AGENTS.md` | root `AGENTS.md`: marked block, brief, commands | `adopt.py init` |
| `CLAUDE.md` | root `CLAUDE.md`, one line | `adopt.py init` |
| `DETAILS.md` | `docs/DETAILS.md`: decisions and gotchas | `adopt.py init` |
| `ISSUES.md` | `docs/ISSUES.md`: debug records | `adopt.py init` |
| `PLANS-README.md` | `docs/plans/README.md` | `adopt.py init` |
| `dev-notes.yml` | `.github/workflows/dev-notes.yml` | `adopt.py init` |
| `PLAN.md` | `docs/plans/YYYY-MM-DD-<slug>.md`, one per task | you, per task |
| `PR.md` | pull request text | you, per change |
| `ROOT-README.md` | root `README.md`, the marketing page | you |
| `FEATURES.md` `USAGE.md` `SETUP.md` `REQUIREMENTS.md` `TESTING.md` `TODO.md` | the optional docs in `docs/` | you, when the repo needs one |
| `FOLDER-README.md` | `<folder>/README.md`, optional | you, when a folder needs one |
| `dev-notes.toml` | `.dev-notes.toml`, optional | you |

Tests: `python -m unittest tests.test_adopt -v` (init copies them; the marked block is stamped with `VERSION`).
