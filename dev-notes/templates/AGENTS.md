# AGENTS.md

<!-- dev-notes:begin v1.2.0 -->
## Dev notes (plan, read, finalize, execute, review)
This repo follows dev-notes v1.2.0 (dev-notes/STANDARD.md). The seven steps:
1. Plan (draft). Read this file, the open plans in docs/plans/, and the Decisions and Gotchas in docs/DETAILS.md. Draft your plan in docs/plans/ (one file per task, named by date and slug) before you open any code.
2. Read the code the draft names, through the language server (text search where none runs). Code is the truth: where a doc disagrees with it, note it in the plan and fix the doc in step 5.
3. Finalize the plan: real files and symbols, subtasks, and the agent that owns each. A design change waits for the owner's OK. No code edits before the plan is final.
4. Execute, in a branch or worktree.
5. Update the docs in the same change: decisions and gotchas in docs/DETAILS.md, bugs in docs/ISSUES.md, the plan's status.
6. Review. A reviewer that did not write the change reviews each subtask before it is marked done, and the whole change before the PR.
7. Iterate. Update the plan first, then the code; repeat 4 to 6 until the review passes. Then run `python dev-notes/tools/check.py`.

Do not push to shared branches, merge, or open PRs unless the owner says so.
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
