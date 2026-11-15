# dev-notes standard

Version 1.0.0 · 2026-11-15 · License: see LICENSE

A documentation-first way for AI agents (and people) to work on a code repo. It works with any agent: Claude Code, Codex, Gemini, Copilot, Cursor, or a human.

## 1. The rule: docs first

**An agent reads the docs and writes its plan into the docs before it opens code.**

1. Read `README.md`, `docs/BRIEF.md`, `docs/PLAN.md`, `docs/TODO.md`, the `docs/ISSUES.md` index, and the `README.md` of each folder the task touches.
2. Write the plan into `docs/PLAN.md`: goal, steps, the files and symbols each step touches.
3. Open code only through the plan: the files and symbols it names, found with a language server (LSP) where one runs, with a stated fallback where none does (section 5, step 3).
4. Never "jump into code": no browsing source to get oriented, no edits before the plan exists.
5. Bring the docs back in line in the same change as the code.

**Why.** The docs are the shared memory between sessions and between agents. Every session starts cold. An agent that reads code without the docs re-derives the design from scratch: slowly, expensively, and often wrongly, then acts on the wrong picture. Writing the plan first also gives the human one place to check direction before any code moves.

### 1.1 Stop conditions

Stop and fix the docs first, or say so and stop, when:

1. `docs/BRIEF.md` or `docs/PLAN.md` is missing. Create them (adoption, section 7) before any code task.
2. The folder you need has no `README.md`, or its "Public surface" does not match the code you find while planning. Fix the folder doc first.
3. The docs contradict each other or the code. Record the conflict in PLAN, decide which is true (ask the owner if unsure), fix the doc, then go on.
4. The goal is unclear, or PLAN.md holds a different task still in progress. Ask; do not overwrite someone else's plan.
5. The work needs code outside the files the plan names. Update the plan first, then open them.
6. The drift check (section 6) fails before you start. Fix it or report it before adding more change.
7. The plan changes design (a decision in DETAILS) and the owner has not agreed to it. Stop after writing the plan.
8. You cannot tell from the docs where to start. That is a doc gap: write the missing doc, then plan.

## 2. Repo layout

```
README.md               marketing page
AGENTS.md               "this repo follows dev-notes": read this first (section 7.3)
CLAUDE.md               one line: @AGENTS.md (Claude Code reads CLAUDE.md, not AGENTS.md)
LICENSE  CHANGELOG.md  THIRD_PARTY_NOTICES.md   root, by convention
docs/
  BRIEF.md  DETAILS.md  FEATURES.md  USAGE.md  SETUP.md  REQUIREMENTS.md
  TESTING.md  PLAN.md  TODO.md  ISSUES.md
  img/                  images for README and docs
  archive/              finished plans, old issues (archive, never delete)
  <SPEC>.md             optional specs: CONTRACT, REFERENCE, file formats
<code folder>/README.md one per package or code folder: brief + tests
dev-notes/              this standard, vendored (section 7)
.dev-notes.toml         optional config for the drift check
```

### 2.1 README.md is a marketing page

Holds: one-line pitch, badges, one to three images, one demo (GIF or short video), a quick start of at most five commands, five to eight feature bullets, links into `docs/`, a license line.
Does not hold: option tables, architecture, test procedures, changelogs, plans. Those live in `docs/`.
Template: [templates/ROOT-README.md](templates/ROOT-README.md).

### 2.2 The docs/ files

| File | Holds | Update when |
|---|---|---|
| [BRIEF](templates/BRIEF.md) | What it is in five lines, the main parts, a status line, the doc map | parts are added or removed; status changes |
| [DETAILS](templates/DETAILS.md) | Architecture, data flow, interfaces, decisions (date, choice, why, rejected), gotchas | structure, data flow or a decision changes |
| [FEATURES](templates/FEATURES.md) | One section per feature: what, limits, since, folder, tests; for UI, the panel priority table | a feature is added, changed or removed |
| [USAGE](templates/USAGE.md) | End-user manual: tasks in the order a user meets them, examples, errors, troubleshooting | user-visible behavior or options change |
| [SETUP](templates/SETUP.md) | Install, developer setup, config keys, env var names (never values) | a dependency, config key or env var changes |
| [REQUIREMENTS](templates/REQUIREMENTS.md) | Runtime minimums and what happens below them; promises other code relies on | a dependency or promise changes |
| [TESTING](templates/TESTING.md) | How to run each suite, what CI runs, gaps, at most three human checks | tests, CI or human checks change |
| [PLAN](templates/PLAN.md) | The current task: goal, steps with files and symbols, status, decisions, open items for the next dev | at the start, at each step, at the end |
| [TODO](templates/TODO.md) | Backlog across tasks, one line each, with a pointer | an item opens or closes |
| [ISSUES](templates/ISSUES.md) | Known bugs: index, then one section per bug in the 7-step debug record | a bug is found, narrowed, fixed, closed |

- **ISSUES.md and an issue tracker.** ISSUES.md is the engineering record (how a bug was narrowed and why the fix is right). The tracker (GitHub Issues etc.) holds outside reports. Cross-link by number; do not mirror.
- **Where other docs go.** A long manual is USAGE. A "simplest way" page is the top of USAGE and the README quick start. Exhaustive option lists go in `docs/REFERENCE.md`, generated from `--help` where possible. Interfaces other programs rely on go in `docs/CONTRACT.md`, versioned. LICENSE, CHANGELOG and THIRD_PARTY_NOTICES stay at the root.
- **Moving a file others link to:** leave a one-line stub at the old path for one release.

### 2.3 One doc per folder: `<folder>/README.md`

Every package (has `__init__.py`, `package.json`, `go.mod`, `Cargo.toml`, ...) and every top-level code or data folder (`tests/`, `tools/`, `examples/`, plugins) has a `README.md` with two sections:

- `## Brief`: what the folder is for, its public surface (a table of names), what it depends on, what uses it.
- `## Tests`: which tests cover it, the command to run just those, known gaps.

README.md, not another name, because code hosts show it when you open the folder and every agent looks for it first. Only the root README is marketing. Not needed for `docs/`, image folders, caches, fixtures and static-asset subfolders (the parent covers them). Template: [templates/FOLDER-README.md](templates/FOLDER-README.md).

### 2.4 Size rules

| File | Max lines | When over |
|---|---|---|
| root README.md | 250 | move detail to USAGE |
| folder README.md | 80 | move detail to DETAILS, or split the folder |
| BRIEF.md | 60 | move detail to DETAILS |
| PLAN.md | 150 | finished plans to `docs/archive/PLAN-YYYY-MM-DD-<slug>.md` |
| ISSUES.md | 300 | closed bugs older than 30 days to `docs/archive/ISSUES-YYYY.md` |
| any other doc | 400 | split into `docs/<name>/` with its own README.md index |

### 2.5 Writing rules

- Answer first, named items, short sentences. No padding.
- One topic, one file. Link instead of repeating.
- Dates YYYY-MM-DD. Status words: `open`, `in progress`, `blocked (why)`, `done YYYY-MM-DD`.
- Relative links. No absolute local paths, private addresses, personal data or secrets in a public repo. Env var names only, never values.
- Keep each template's headings so agents can find sections.

## 3. Private notes (optional)

Some notes must not ship: a maintainer's local paths, live test setups, unpublished plans, notes on other private work. Keep them outside the repo, or in a folder listed in `.gitignore` (your own repo) or `.git/info/exclude` (someone else's repo). The public docs then link to nothing private; the private notes point at the public docs. Before private text moves into a public doc, scrub it: paths, network addresses, names, private project names, unpublished plans, secrets. The drift check can scan for patterns kept in a file outside the repo (`--scrub-file`).

In a fork of someone else's project, the upstream owns `docs/`. Keep your dev-notes privately under these names and change upstream docs only inside a feature PR, in their style.

## 4. Roles

- **Owner (human):** approves plans that change design, does at most three judgment checks per change, decides merges and anything public (upstream PRs, releases, new repos).
- **Agent:** everything else, including tests and docs. It never pushes to a shared branch, merges, or opens a PR on someone else's repo unless the owner says so. Never force-push or rewrite shared history.

## 5. The development loop

1. **Read, then state the big picture.** The files in rule 1.1. Write five lines: what the project is; where the task sits; which parts it touches; what is open nearby (TODO, ISSUES ids); the main risk. List the files and symbols to open next, at most ten.
2. **Plan in the docs.** Write `docs/PLAN.md` (archive the previous finished plan). For UI work, write each panel's priority table first: what the user needs most is visible, secondary is compact, the rest is folded. For a bug, open the ISSUES entry with steps 1-3 of the debug record first.
3. **Detailed planning with LSP.** Use the language server to open only what matters: workspace symbol search for entry points, go to definition, find references to size the change, call hierarchy for callers, diagnostics for what is already broken. Record the symbols in the plan.
   - Fallback when no language server runs: the folder README's public surface and "used by", then a text search for the definition (`def <name>`, `class <name>`, `function <name>`) and for references (`<name>(`), then file globs. For diagnostics, run the project's linter or type checker. Say which method you used.
4. **Execute in a branch or worktree.** Never in a checkout a running service uses. One branch per task. For a bug: the failing test first, then the fix.
5. **Update the docs in the same change:** folder READMEs, FEATURES, USAGE, SETUP, REQUIREMENTS, TESTING, DETAILS, TODO, ISSUES, PLAN status and "open for the next dev".
6. **Verify.** Tests for the touched folders, then the full suite, then CI. UI changes get a scripted browser run and a screenshot review by an agent. The owner gets at most three one-line judgment checks, listed in TESTING.md.
7. **Draft the PR** from [templates/PR.md](templates/PR.md). Push and merge only as the owner's rules allow (section 4). Follow the target project's rules on commit trailers and AI attribution.

### 5.1 Debug record (ISSUES.md)

1. Triage: symptom, where, how often.
2. Narrow, with a delta: last good vs first bad, what changed between, which combination breaks it. Evidence, not a guess. No expensive runs before this step.
3. Document: brief and details.
4. Hypotheses, as a table.
5. Test each; update 1-4 as results come in.
6. Review: other scenarios, side effects of the fix, missed hypotheses.
7. Verify the fix path; name the test that proves it.

### 5.2 Definition of done

- [ ] PLAN.md status current; "open for the next dev" filled.
- [ ] Each touched folder's README matches its public surface and tests.
- [ ] FEATURES, USAGE, SETUP, REQUIREMENTS, TESTING, DETAILS updated where behavior, setup or structure changed.
- [ ] TODO and ISSUES current.
- [ ] New behavior has a test; local suite and CI green.
- [ ] UI checked by script and screenshots; at most three human checks listed.
- [ ] Drift check passes; no private data in public docs.
- [ ] PR text drafted.

## 6. Drift check: `dev-notes/tools/check.py`

```
python dev-notes/tools/check.py [--repo PATH] [--base REF] [--strict] [--config FILE] [--scrub-file FILE] [--format text|json]
```

- Python 3.9+, standard library only, so CI needs no install.
- Exit 0 = clean (warnings may print), 1 = errors (or warnings with `--strict`), 2 = bad usage.
- One line per finding: `ERROR|WARN <check-id> <path>:<line> <message>`.

| Id | Check | Level |
|---|---|---|
| D1 | Required files exist: README.md, AGENTS.md, docs/ BRIEF ... ISSUES | error |
| D2 | Every package/code folder has README.md with `## Brief` and `## Tests` | error |
| D3 | Relative links, images and #anchors resolve in all docs | error |
| D4 | Size limits (2.4) | warn |
| D5 | With `--base`: code changed under folder X but X/README.md and docs/ did not | warn |
| D6 | With `--base`: code changed but docs/PLAN.md not touched; new test file not named in TESTING.md or a folder README | warn |
| D7 | Names in a folder README "Public surface" table exist in that folder's code | warn |
| D8 | Scrub patterns (from `--scrub-file`) absent from tracked docs | error |
| D9 | `dev-notes/VERSION` matches the version AGENTS.md states | warn |

`.dev-notes.toml` (optional): `skip_folders`, `code_extensions`, `extra_required_docs`, size limit overrides.

GitHub Actions, `.github/workflows/dev-notes.yml`:

```yaml
name: dev-notes
on:
  pull_request:
  push:
    branches: [main]
jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Drift check
        run: python dev-notes/tools/check.py --base "origin/${{ github.base_ref || 'main' }}"
```

## 7. Adopting the standard

### 7.1 Mechanism: a vendored copy with a version stamp (recommended)

`tools/adopt.py` copies the standard into the repo's `dev-notes/` folder and stamps the version. Later, `update` replaces that folder with a newer version as an ordinary, reviewable commit.

Why this and not the others:
- **Every agent sees it.** A plain `git clone` has the files. Submodules come up empty unless cloned with `--recurse-submodules`, and many cloud agents and CI checkouts skip that.
- **No contributor friction.** No detached submodule HEADs, no subtree merges, nothing for a fork's upstream to learn.
- **Updates are explicit.** Each repo moves to a new version when its owner merges the update commit, and the diff shows what changed.
- **It works for existing repos.** A template repo only helps on day one and cannot push later versions.

Costs: a small duplicated folder per repo, and versions can lag. D9 flags the lag.

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

`init` writes: `dev-notes/` (STANDARD.md, templates/, tools/check.py, VERSION); `AGENTS.md` (or adds the marked block to an existing one); `CLAUDE.md` (`@AGENTS.md`) if absent; every missing `docs/` file from templates; `.github/workflows/dev-notes.yml` if absent. It never overwrites an existing doc.
`update` replaces only `dev-notes/` and the marked block in AGENTS.md. It never touches `docs/` or folder READMEs.

### 7.3 Files the adopting repo carries

`AGENTS.md` (the cross-agent convention: Codex, Copilot and Cursor read it; Gemini CLI can be pointed at it with `contextFileName`; Claude Code reads it through CLAUDE.md). The marked block, from [templates/AGENTS.md](templates/AGENTS.md):

```markdown
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
```

`CLAUDE.md`: one line, `@AGENTS.md`. Repo-specific rules go in AGENTS.md outside the marked block.

### 7.4 Example agent

[agents/claude/dev-notes.md](agents/claude/dev-notes.md) is a Claude Code subagent with three modes: `init` (set a repo up from what exists), `brief` (big picture + plan before a task), `close` (update docs, run the drift check, draft the PR). Copy it to `.claude/agents/` in a repo or `~/.claude/agents/`. Other agents can follow the same three modes from this text.
