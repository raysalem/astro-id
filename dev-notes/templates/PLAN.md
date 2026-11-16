# Plan: <task>

Goal: <one sentence>. Branch: `<branch>`. Planner: <agent or person>. Started YYYY-MM-DD. Status: <draft | final | in progress | blocked (why) | done YYYY-MM-DD>.

## Big picture
1. Project: <what it is>
2. Task: <where it sits>
3. Touches: <parts / folders>
4. Nearby: <open plans, ISSUES ids>
5. Risk: <main risk>

## Code read
Navigation: <LSP | text search>. Filled after the draft, before the plan is final.
- `<file or symbol>`: <what it told you; what the draft had wrong>

## Subtasks
| # | Subtask | Files / symbols | Agent | Depends on | Status | Review |
|---|---|---|---|---|---|---|
| 1 | <write the failing test> | `tests/test_x.py::test_y` | <owner> | none | open | <reviewer, pass / changes needed, date, attempt n> |

Subtasks run at the same time only if their files do not overlap and neither changes what the other calls; each in its own worktree. An agent edits only the Status of its own row. The planner writes everything else, including the reviewer's verdicts.

## Decisions
- <decision>: <why>. Design changes need the owner's OK before code.

## Review of the whole change
- <reviewer, verdict, date, attempt n, what was sent back>

## Open for the next dev
- <what is left, where to start, what to read first>
