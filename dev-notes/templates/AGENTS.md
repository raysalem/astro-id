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
<What it is, one sentence.>
<Who uses it and for what, one sentence.>
<How it works at the top level, one or two sentences.>

| Part | Folder | Job |
|---|---|---|
| <part> | `<folder>/` | <one line> |

## Commands
| Do | Command |
|---|---|
| Install | `<install command>` |
| Test | `<test command>` |
| Lint / type check | `<lint command>` |

## Repo-specific rules
- <conventions, anything this repo adds>
