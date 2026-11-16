# dev-notes standard

Version 1.1.0 · 2026-11-15 · License: see LICENSE

A small way for AI agents (and people) to work on a code repo: plan before edits, and write down what the code cannot tell the next session. It works with any agent: Claude Code, Codex, Gemini, Copilot, Cursor, or a human.

## 1. The rule: plan before edits

**An agent reads the notes, looks at the code, and writes its plan before it changes anything.**

1. Read `AGENTS.md` (brief, commands, rules), the open plans in `docs/plans/`, and the Decisions and Gotchas in `docs/DETAILS.md`. For a bug, the `docs/ISSUES.md` index too.
2. Look at the code the task touches, read-only: with a language server (LSP) where one runs, a text search where none does. **Code is the truth.** Where a doc disagrees with the code, the doc is wrong: fix the doc.
3. Write the plan to `docs/plans/YYYY-MM-DD-<slug>.md`: goal, steps, the files and symbols each step touches.
4. No edits before the plan exists. A plan that changes design waits for the owner.
5. In the same change as the code: record what the code cannot say (decisions, gotchas, bugs) and set the plan's status.

**Why.** Every session starts cold. Code shows what the program does, never why it is that way, what was tried and rejected, or what is half done. Those three things are what the notes hold. Everything else an agent reads from the code, which is always current. Writing the plan first gives the owner one place to check direction before any code moves.

**What changed from 1.0.0, and why.** 1.0.0 required ten docs, a README in every code folder, and planning before reading code. A two-model review found the required set cost more to keep current than it saved, the planning rule made agents plan from summaries instead of the code, one shared plan file blocked parallel sessions, and the check passed on unfilled templates. 1.1.0 keeps the parts with no substitute and makes the rest optional.

### 1.1 Stop conditions

Stop and say so when:

1. `AGENTS.md` or `docs/DETAILS.md` is missing. Adopt first (section 7).
2. The goal is unclear.
3. An open plan names the files or symbols you need. Ask its owner; never edit someone else's open plan.
4. The work needs code outside what your plan names. Update the plan first.
5. The plan changes design (a Decision in DETAILS) and the owner has not agreed. Stop after writing the plan.
6. The check (section 6) reports errors before you start. Fix them or report them before adding more change.

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
| [docs/plans/](templates/PLAN.md) | One file per task, `YYYY-MM-DD-<slug>.md`: goal, steps with files and symbols, status, what is open for the next dev | at the start, at each step, at the end |

- **One plan per task.** Two sessions never share a plan file, so parallel work does not collide. A finished plan stays where it is: `docs/plans/` is also the archive.
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
- Dates YYYY-MM-DD. Status words: `open`, `in progress`, `blocked (why)`, `done YYYY-MM-DD`.
- Relative links. No absolute local paths, private addresses, personal data or secrets in a public repo. Env var names only, never values.
- Archive, never delete: superseded docs go to `docs/archive/`.

## 3. Private notes (optional)

Some notes must not ship: a maintainer's local paths, live test setups, unpublished plans, notes on other private work. Keep them outside the repo, or in a folder listed in `.gitignore` (your own repo) or `.git/info/exclude` (someone else's repo). Do not name that folder `dev-notes/`: that name is the vendored standard's. If it is a link (symlink, junction), the tools never follow it. Before private text moves into a public doc, scrub it. The check can scan tracked files for patterns kept in a file outside the repo (`--scrub-file`).

In a fork of someone else's project, the upstream owns `docs/`. Keep your notes privately under these names and change upstream docs only inside a feature PR, in their style.

## 4. Roles

- **Owner (human):** approves plans that change design, does at most three judgment checks per change, decides merges and anything public (upstream PRs, releases, new repos).
- **Agent:** everything else, including tests and notes. It never pushes to a shared branch, merges, or opens a PR on someone else's repo unless the owner says so. Never force-push or rewrite shared history.

## 5. The development loop

1. **Read.** `AGENTS.md`, open plans, DETAILS decisions and gotchas.
2. **Look at the code.** Language server: workspace symbols for entry points, definition, references, call hierarchy, diagnostics. No language server: text search for the definition and its references, then file globs; the project's linter or type checker for diagnostics.
3. **Write the plan** in `docs/plans/`. Five lines of big picture (project; where the task sits; parts touched; open plans and issues nearby; the main risk), then the steps with files and symbols. For a bug, open the ISSUES entry with steps 1-3 of the debug record first.
4. **Execute in a branch or worktree.** Never in a checkout a running service uses. One branch per task. For a bug: the failing test first, then the fix.
5. **Close the notes in the same change.** New decisions and gotchas into DETAILS. ISSUES current. AGENTS.md if parts or commands changed. Any optional doc the change made wrong. The plan's status and "open for the next dev".
6. **Review for truth.** Reread what you wrote in step 5 against the diff. The check cannot do this; it is the step that keeps the notes honest.
7. **Verify.** Tests, then CI. UI changes get a scripted browser run and a screenshot review by an agent. The owner gets at most three one-line judgment checks.
8. **Draft the PR** from [templates/PR.md](templates/PR.md). Push and merge only as the owner's rules allow (section 4). Follow the target project's rules on commit trailers and AI attribution.

### 5.1 Debug record (ISSUES.md)

1. Triage: symptom, where, how often.
2. Narrow, with a delta: last good vs first bad, what changed between, which combination breaks it. Evidence, not a guess. No expensive runs before this step.
3. Document: brief and details.
4. Hypotheses, as a table.
5. Test each; update 1-4 as results come in.
6. Review: other scenarios, side effects of the fix, missed hypotheses.
7. Verify the fix path; name the test that proves it.

### 5.2 Definition of done

- [ ] The plan's status is current; "open for the next dev" is filled.
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
- **It is mechanical.** It can tell that a note exists, is filled in and links correctly. It cannot tell whether a note is true. That is loop step 6.

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

**Coming from 1.0.0:** run `update`; move `docs/PLAN.md` to `docs/plans/` under a dated name; fold `docs/BRIEF.md` into the Brief section of `AGENTS.md` and archive it; keep the other docs only if they earn their place (2.2); copy the new workflow file by hand. Until then the check still accepts `docs/PLAN.md` as a plan.

### 7.3 AGENTS.md

`AGENTS.md` is the cross-agent convention: Codex, Copilot and Cursor read it; Gemini CLI can be pointed at it with `contextFileName`; Claude Code reads it through `CLAUDE.md` (`@AGENTS.md`). The marked block comes from [templates/AGENTS.md](templates/AGENTS.md); `update` rewrites only that block. Everything outside it (brief, commands, repo rules) is yours.

### 7.4 Example agent

[agents/claude/dev-notes.md](agents/claude/dev-notes.md) is a Claude Code subagent with three modes: `init` (set a repo up from what exists), `brief` (big picture and plan before a task), `close` (close the notes, review them against the diff, run the check, draft the PR). Its rules are in its own text, so it does not reread this file on every run. Copy it to `.claude/agents/` in a repo or `~/.claude/agents/`.
