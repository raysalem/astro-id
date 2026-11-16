# dev-notes standard

Version 1.2.0 · 2026-11-15 · License: see LICENSE

A small way for AI agents (and people) to work on a code repo: draft a plan, read the code, finalize the plan, execute, update the notes, get it reviewed. The notes hold what the code cannot tell the next session. It works with any agent: Claude Code, Codex, Gemini, Copilot, Cursor, or a human.

## 1. The rule: plan, read, finalize, execute, review

**Plan. Read code via LSP. Finalize the plan. Execute. Update docs. Review. Iterate.**

1. **Plan (draft).** Read `AGENTS.md` (brief, commands, rules), the open plans in `docs/plans/`, and the Decisions and Gotchas in `docs/DETAILS.md`; for a bug, the `docs/ISSUES.md` index. Write a draft plan to `docs/plans/YYYY-MM-DD-<slug>.md` from the notes alone: goal, big picture, the steps as you understand them, and what you need to look at. No code is open yet.
2. **Read the code the draft names.** Through a language server (LSP): workspace symbols, definition, references, call hierarchy, diagnostics. Where no server runs, a text search, and say so. Read-only and bounded: what the draft names, about ten files or symbols; if you need more, add it to the draft first. **Code is the truth.** Where a doc disagrees with the code, the doc is wrong: note it in the plan and fix the doc in step 5.
3. **Finalize the plan.** Replace guesses with the real files and symbols. Split the work into subtasks. Where several agents will work, give each subtask one owner and say what it depends on (1.2). A plan that changes design waits for the owner. **No code edits before the plan is final.** The plan itself, and for a bug the ISSUES entry, are the only files written before that.
4. **Execute.** Branch or worktree; for a bug, the failing test first.
5. **Update the docs in the same change.** Decisions, gotchas, bugs, and the plan's status.
6. **Review.** A reviewer that did not write the change reviews each subtask before it is marked done, and the whole change before the PR (1.3).
7. **Iterate.** When the code, a test or the reviewer proves the plan wrong, update the plan first, then the code. Repeat 4-6 until the review passes.

**Why this order.** Every session starts cold. The draft comes first so the agent enters the code with a question and a boundary, not to wander: reading code to "get oriented" is slow and rebuilds the design from accidents. The code is read before the plan is final so the plan rests on real signatures, not on a summary that may be stale. The reviewer is a second reader because the author of a change is the worst judge of whether its notes are true.

**History.** 1.0.0 planned without reading code and required ten docs. 1.1.0 cut the required set and let agents read code before any plan existed. 1.2.0 keeps the 1.1.0 set and sets the order above (the owner's call, 2026-11-15).

### 1.1 Stop conditions

Stop and say so when:

1. `AGENTS.md` or `docs/DETAILS.md` is missing. Adopt first (section 7).
2. The goal is unclear.
3. Another task's open plan names the files or symbols you need. Ask its planner; never edit another task's plan. (In your own task's plan you edit only your own row, 1.2.)
4. The work needs code outside what your plan names. Update the plan first.
5. The plan changes design (a Decision in DETAILS) and the owner has not agreed. Stop after finalizing the plan.
6. The check (section 6) reports errors before you start. Fix them or report them before adding more change.
7. The reviewer has sent the same subtask back three times. The plan is wrong, not the code: stop and ask.

### 1.2 Plans for several agents

One plan can drive several agents. The agent that writes the plan is the planner.

- Each subtask has one owner, a list of files and symbols, and the subtasks it depends on.
- Two subtasks run at the same time only if their file lists do not overlap **and** neither changes a symbol or interface the other uses. Otherwise the plan orders them.
- Agents that run at the same time each work in their own worktree (or clone), on their own branch. Never two agents in one checkout.
- An agent edits only the files its subtask names, and in the plan only the Status of its own row.
- The shared notes have one writer, the planner: `docs/DETAILS.md`, `docs/ISSUES.md`, `AGENTS.md`, optional docs, and every part of the plan outside an agent's own row (steps, Decisions, Review cells, the whole-change review, "Open for the next dev"). An agent reports its new decisions, gotchas and bugs to the planner in its result; the planner writes them when integrating. With one agent, that agent is the planner.
- An agent that needs a file outside its subtask stops and reports to the planner, who updates the plan.

### 1.3 Review

The reviewer is an agent (or a person) that did not write the change. It has read access only.

- **When:** before a subtask is marked done, and once more over the whole change before the PR is drafted.
- **What it checks:** the diff does what the subtask says and nothing else; the tests exist and pass (it runs them); every doc line the change wrote or could have made false is true against the diff; no private data.
- **What it returns:** `pass`, or `changes needed` with findings that name a file and line. It does not fix anything.
- **Record:** the reviewer edits nothing. The planner (or a single author) copies its verdict into the plan: the subtask's Review cell as reviewer, verdict, date, attempt number; the whole-change review into the plan's "Review of the whole change" section.
- **After a plan change:** when step 7 changes a subtask, its earlier review no longer counts. Review it again, and the whole change again at the end.

The check (section 6) is not the review. The check is mechanical and cannot tell whether a note is true.

## 2. Repo layout

```
README.md               marketing page
AGENTS.md               rules, brief, commands: read first (section 7.3)
CLAUDE.md               one line: @AGENTS.md (Claude Code reads CLAUDE.md, not AGENTS.md)
docs/
  DETAILS.md            decisions and gotchas
  ISSUES.md             debug records
  plans/                one plan per task; finished plans stay
  <optional docs>       FEATURES USAGE SETUP REQUIREMENTS TESTING TODO, specs
dev-notes/              this standard, vendored (section 7)
.dev-notes.toml         optional config for the check
```

### 2.1 Required

| File | Holds | Update when |
|---|---|---|
| `README.md` | The marketing page: pitch, quick start, links into `docs/` | the pitch or quick start changes |
| [AGENTS.md](templates/AGENTS.md) | The marked block (the rules), a brief of a few lines, the main parts, the install / test / lint commands, repo rules. It is loaded into every session, so keep it short | parts or commands change |
| [docs/DETAILS.md](templates/DETAILS.md) | Decisions (date, choice, why, rejected) and gotchas. Not a description of the code | a decision is made or reversed; something surprised you |
| [docs/ISSUES.md](templates/ISSUES.md) | Known bugs: an index, then one section per bug in the debug record (5.1) | a bug is found, narrowed, fixed, closed |
| [docs/plans/](templates/PLAN.md) | One file per task, `YYYY-MM-DD-<slug>.md`: goal, the code read, subtasks with files, symbols, owner and review, status, what is open for the next dev | as a draft at the start, when finalized, at each subtask, at the end |

- **One plan per task.** Two tasks never share a plan file, so parallel work does not collide. Agents working on the same task share its plan under the rules in 1.2. A finished plan stays where it is: `docs/plans/` is also the archive.
- **ISSUES.md and an issue tracker.** ISSUES.md is the engineering record (how a bug was narrowed and why the fix is right). The tracker holds outside reports. Cross-link by number; do not mirror.

### 2.2 Optional

Add one when the repo needs it; an empty or stale doc is worse than none. The check treats a doc that exists like any other (links, size, unfilled lines).

| File | Worth having when |
|---|---|
| [FEATURES](templates/FEATURES.md) | users need a list of what it does and its limits; a UI needs panel priority tables |
| [USAGE](templates/USAGE.md) | there are end users and more to say than the README quick start |
| [SETUP](templates/SETUP.md) | install or configuration is more than the commands in AGENTS.md |
| [REQUIREMENTS](templates/REQUIREMENTS.md) | other code relies on promises, or minimum versions matter |
| [TESTING](templates/TESTING.md) | there are several suites, CI jobs, or human checks to list |
| [TODO](templates/TODO.md) | there is a backlog and no issue tracker holds it |
| [folder README](templates/FOLDER-README.md) | a folder has a purpose or gotcha its code does not show |
| `docs/CONTRACT.md`, `docs/REFERENCE.md` | other programs rely on an interface; there is an exhaustive option list |

Do not keep tables of function or class names in docs. A language server or a search reads them from the code, and a copy goes stale.

### 2.3 Size rules

| File | Max lines | When over |
|---|---|---|
| root README.md | 250 | move detail to USAGE |
| AGENTS.md | 120 | move detail to DETAILS or an optional doc |
| a plan | 150 | split the task |
| ISSUES.md | 300 | closed bugs older than 30 days to `docs/archive/ISSUES-YYYY.md` |
| folder README.md | 80 | it is describing code; cut it |
| any other doc | 400 | split into `docs/<name>/` with its own README.md index |

### 2.4 Writing rules

- Answer first, named items, short sentences. No padding.
- One topic, one file. Link instead of repeating.
- Dates YYYY-MM-DD. Plan status: `draft`, `final`, `in progress`, `blocked (why)`, `done YYYY-MM-DD`. Subtasks and backlog items also use `open`.
- Relative links. No absolute local paths, private addresses, personal data or secrets in a public repo. Env var names only, never values.
- Archive, never delete: superseded docs go to `docs/archive/`.

## 3. Private notes (optional)

Some notes must not ship: a maintainer's local paths, live test setups, unpublished plans, notes on other private work. Keep them outside the repo, or in a folder listed in `.gitignore` (your own repo) or `.git/info/exclude` (someone else's repo). Do not name that folder `dev-notes/`: that name is the vendored standard's. If it is a link (symlink, junction), the tools never follow it. Before private text moves into a public doc, scrub it. The check can scan tracked files for patterns kept in a file outside the repo (`--scrub-file`).

In a fork of someone else's project, the upstream owns `docs/`. Keep your notes privately under these names and change upstream docs only inside a feature PR, in their style.

## 4. Roles

- **Owner (human):** approves plans that change design, does at most three judgment checks per change, decides merges and anything public (upstream PRs, releases, new repos).
- **Agent:** everything else, including tests and notes. It never pushes to a shared branch, merges, or opens a PR on someone else's repo unless the owner says so. Never force-push or rewrite shared history.

## 5. The development loop

| # | Step | Output |
|---|---|---|
| 1 | Plan (draft): read the notes, write the draft plan | `docs/plans/<plan>.md`, status `draft` |
| 2 | Read the code the draft names, via LSP | the plan's "Code read" list |
| 3 | Finalize the plan: files, symbols, subtasks, owners | status `final`; owner's OK if design changes |
| 4 | Execute each subtask in a branch or worktree | code and tests |
| 5 | Update docs in the same change (several agents: the planner writes the shared notes) | DETAILS, ISSUES, AGENTS.md, optional docs, the plan's status |
| 6 | Review each subtask, then the whole change | the plan's Review cells and whole-change review |
| 7 | Iterate: plan first, then code, until the review passes | |
| 8 | Verify, then draft the PR | tests, CI, the check, PR text |

Diagram: [docs/img/loop.drawio](docs/img/loop.drawio) (open with draw.io / diagrams.net). Three lanes: documentation, work, review. Work moves only when the docs say so and a second reader agrees.

- **Step 1.** Five lines of big picture (project; where the task sits; parts touched; open plans and issues nearby; the main risk), then the steps as the notes suggest them. For a bug, open the ISSUES entry with steps 1-3 of the debug record first; with the plan, it is the only file written before the plan is final.
- **Step 2.** No language server: text search for the definition and its references, then file globs; the project's linter or type checker for diagnostics. Write in the plan which you used.
- **Step 4.** Never in a checkout a running service uses. One branch per subtask owner. For a bug: the failing test first, then the fix.
- **Step 5.** New decisions and gotchas into DETAILS. ISSUES current. AGENTS.md if parts or commands changed. Any optional doc the change made wrong.
- **Step 8.** Tests, then CI. UI changes get a scripted browser run and a screenshot review by an agent. The owner gets at most three one-line judgment checks. Draft the PR from [templates/PR.md](templates/PR.md). Push and merge only as the owner's rules allow (section 4). Follow the target project's rules on commit trailers and AI attribution.

### 5.1 Debug record (ISSUES.md)

1. Triage: symptom, where, how often.
2. Narrow, with a delta: last good vs first bad, what changed between, which combination breaks it. Evidence, not a guess. No expensive runs before this step.
3. Document: brief and details.
4. Hypotheses, as a table.
5. Test each; update 1-4 as results come in.
6. Review: other scenarios, side effects of the fix, missed hypotheses.
7. Verify the fix path; name the test that proves it.

### 5.2 Definition of done

- [ ] The plan is final, its status is current, and "open for the next dev" is filled.
- [ ] Every subtask has a passing review in its Review cell; the whole change has one too.
- [ ] New decisions and gotchas are in DETAILS; ISSUES is current.
- [ ] AGENTS.md and any optional doc still match the code the change touched.
- [ ] New behavior has a test; local suite and CI green.
- [ ] The check passes; no private data in public docs.
- [ ] PR text drafted.

## 6. The check: `dev-notes/tools/check.py`

```
python dev-notes/tools/check.py [--repo PATH] [--base REF] [--strict] [--config FILE] [--scrub-file FILE] [--format text|json]
```

- Python 3.9+, standard library only, so CI needs no install.
- Exit 0 = clean (warnings may print), 1 = errors (or warnings with `--strict`), 2 = bad usage or bad config.
- One line per finding: `ERROR|WARN <check-id> <path>:<line> <message>`.
- **It is mechanical.** It can tell that a note exists, is filled in and links correctly. It cannot tell whether a note is true. That is the review (1.3).

| Id | Check | Level |
|---|---|---|
| D1 | Required files exist: README.md, AGENTS.md, docs/DETAILS.md, docs/ISSUES.md | error |
| D3 | Relative links, images and #anchors resolve in all docs | error |
| D4 | Size limits (2.3) | warn |
| D6 | With `--base`: code changed but no plan under `docs/plans/` was added or updated | warn |
| D8 | Scrub patterns (from `--scrub-file`) absent from tracked files | error |
| D9 | The version in the AGENTS.md block matches `dev-notes/VERSION` | warn |
| D10 | No doc line is still identical to a template placeholder line | error |

D2, D5 and D7 (folder READMEs and their name tables) were retired in 1.1.0; the ids are not reused. D9 catches a half-applied update. To see whether a repo is behind the latest version, run `adopt.py status`. Links and junctions are never followed.

`.dev-notes.toml` (optional): `skip_folders`, `code_extensions`, `extra_required_docs`, `[size_limits]` overrides.

GitHub Actions, `.github/workflows/dev-notes.yml`: [templates/dev-notes.yml](templates/dev-notes.yml). On a pull request it compares with the target branch; on a push, with the commit before the push.

## 7. Adopting the standard

### 7.1 Mechanism: a vendored copy with a version stamp

`tools/adopt.py` copies the standard into the repo's `dev-notes/` folder and stamps the version. Later, `update` replaces that folder with a newer version as an ordinary, reviewable commit.

| Option | Good | Bad |
|---|---|---|
| Vendored copy + VERSION (chosen) | in every clone; explicit updates | duplicated files; manual updates |
| Git submodule | one source; pinned commit | empty in plain clones; detached HEAD; cloud agents miss it |
| Git subtree | files in every clone | heavy merges; easy to edit by mistake; awkward updates |
| Template repo | one click for new repos | no updates; useless for existing repos |

### 7.2 Commands

```
python <dev-notes clone>/tools/adopt.py init   --repo PATH   # first time
python <dev-notes clone>/tools/adopt.py update --repo PATH   # move to the clone's version
python <dev-notes clone>/tools/adopt.py status --repo PATH   # vendored vs latest version, missing files
```

`init` writes: `dev-notes/` (STANDARD.md, templates/, tools/check.py, VERSION, LICENSE); `AGENTS.md` (or adds the marked block to an existing one); `CLAUDE.md` (`@AGENTS.md`) if absent; `docs/DETAILS.md`, `docs/ISSUES.md` and `docs/plans/README.md` if absent; `.github/workflows/dev-notes.yml` if absent. It never overwrites an existing doc. The check then fails (D10) until the copied templates are filled in.
`update` replaces only `dev-notes/` and the marked block in AGENTS.md. It never touches `docs/`, and it does not refresh the workflow file.
Both refuse a `dev-notes/` that is a link, or a folder without a VERSION file: that is someone's own notes, not a vendored copy.

**Coming from 1.1.0:** run `update`; nothing in `docs/` has to move. New plans use the 1.2.0 template.

**Coming from 1.0.0:** run `update`; move `docs/PLAN.md` to `docs/plans/` under a dated name; fold `docs/BRIEF.md` into the Brief section of `AGENTS.md` and archive it; keep the other docs only if they earn their place (2.2); copy the new workflow file by hand. Until then the check still accepts `docs/PLAN.md` as a plan.

### 7.3 AGENTS.md

`AGENTS.md` is the cross-agent convention: Codex, Copilot and Cursor read it; Gemini CLI can be pointed at it with `contextFileName`; Claude Code reads it through `CLAUDE.md` (`@AGENTS.md`). The marked block comes from [templates/AGENTS.md](templates/AGENTS.md); `update` rewrites only that block. Everything outside it (brief, commands, repo rules) is yours.

### 7.4 Example agents

Two Claude Code subagents, in `agents/claude/`. Each carries its rules in its own text, so it does not reread this file on every run. Copy them to `.claude/agents/` in a repo or `~/.claude/agents/`.

- [dev-notes](agents/claude/dev-notes.md): `init` (set a repo up from what exists), `brief` (draft the plan, read the code, finalize the plan), `close` (update the notes, run the check, draft the PR).
- [dev-notes-reviewer](agents/claude/dev-notes-reviewer.md): read-only. Reviews one subtask or the whole change against the plan and returns `pass` or `changes needed`.
