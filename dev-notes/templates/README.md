# templates: files copied into adopting repos

## Brief
One template per doc in the standard, plus the pieces `adopt.py init` writes. Placeholders are in angle brackets. Template headings are kept so agents can find sections.

Public surface:

| Name | Kind | Use |
|---|---|---|
| `BRIEF.md` | doc | `docs/BRIEF.md` |
| `DETAILS.md` | doc | `docs/DETAILS.md` |
| `FEATURES.md` | doc | `docs/FEATURES.md` |
| `USAGE.md` | doc | `docs/USAGE.md` |
| `SETUP.md` | doc | `docs/SETUP.md` |
| `REQUIREMENTS.md` | doc | `docs/REQUIREMENTS.md` |
| `TESTING.md` | doc | `docs/TESTING.md` |
| `PLAN.md` | doc | `docs/PLAN.md` |
| `TODO.md` | doc | `docs/TODO.md` |
| `ISSUES.md` | doc | `docs/ISSUES.md` |
| `AGENTS.md` | doc | root `AGENTS.md`, with the marked block |
| `CLAUDE.md` | doc | root `CLAUDE.md`, one line |
| `ROOT-README.md` | doc | root `README.md`, the marketing page (you write it) |
| `FOLDER-README.md` | doc | `<folder>/README.md` for each code folder (you write it) |
| `PR.md` | doc | pull request text |
| `dev-notes.yml` | CI | `.github/workflows/dev-notes.yml` |
| `dev-notes.toml` | config | `.dev-notes.toml`, optional |

Depends on: nothing. Used by: `tools/adopt.py`, the example agent.

## Tests
- Covered by: `tests/test_adopt.py` (init copies them; the marked block is stamped with `VERSION`).
- Run just these: `python -m unittest tests.test_adopt -v`.
- Gaps: placeholders are not validated.
