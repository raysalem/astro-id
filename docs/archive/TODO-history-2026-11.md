# TODO history (older work streams, 2026-11-09 ... 15)

*Moved from the maintainer's private working notes on 2026-11-15 and scrubbed for this public repo. Sections are dated; later sections replace earlier ones where they say so.*


## Engine
- Horizon-based time pin (detect the foreground/sky boundary, fit altitude 0). Only the Moon pins `best_fit_time` today, and no Moon was in a solved frame of this batch.
- Twilight/foreground frames (DSC07627, DSC07630) do not solve: a "bright-object solve" could match Venus/Jupiter/Mars/1st-mag stars against the sky at a known time.
- Badly defocused frames (DSC07612) solve with RMS > 2 px: a donut-centroid detector would help.
- RAW without a JPG is decoded with rawpy (half size); untested on a real RAW-only folder.
- astrometry.net fallback is implemented but untested (no key here); on first use check the y-axis convention of its WCS (both conventions are tried and the better refit wins).
- Linux/macOS ASTAP discovery is minimal (`ASTAP_PATH`, PATH, /opt/astap).
- B-E1 small photos (branch small-photo-solve b156091, pushed, not merged): the owner to test, then merge. After merge: the examples branch can ship all five frames at 2400 px. `quality.py` still halves every image, so quality metrics on small copies are untested. 07626@2000 solves at grade C with a weak fit (RMS 23 px).

## Facet plugin
- Integration: install into facet-wintest, config block, dry-run backfill on the TDS folder (later agent).
- Verify on the real DB that the viewer shows the tags and the tag dropdown cache refreshes.
- A distinct `source='astro-id'` would need a Facet schema change; deferred.

### Facet as source of truth (2026-11-09)
- Facet's date_taken has no timezone: a Facet-side timezone (from the Set location pin) would remove the dependence on json/site inference.
- Verify on the real DB that GPS-from-EXIF photos pass sane coordinates.

### Tag hierarchy + plugin-controlled runs (2026-11-10)
- Overlay: asterism labels can collide with constellation labels on crowded wide frames; a label-placement pass shared by all kinds would help.
- Stacking only happens in folder runs (the plugin runs a folder when >= `batch_min` frames are pending there); single-file runs (manual Facet date, catch-up of 1-3 photos) are solved individually.
- Facet multi-pass rescans of existing rows keep `scanned_at`, so "rescanned since" detection only catches new inserts; a forced re-solve is the backfill CLI.
- `--pending` without `--since` would tag every astro-category photo in the library, including misclassified ones (IMG_3155, a birthday photo with category astro, would get `plate-solve failed`). The exit hook always passes `--since`.
- Planets fainter than mag 6 (Neptune) are level 2 and always taggable when found; revisit if false positives appear.

### Annotations, RAW source, plugin config (2026-11-10)
- [x] Real backfill run 2026-11-10 10:01 after Facet's annotations merged into local/win-test: 15 rows. `upsert_annotation` accepts a dict or JSON text and does not commit (checked against facet-wintest `db/annotations.py`).
- Stars group holds only the ~100 IAU-named stars; an unlabeled Hipparcos star layer (mag slider) would need Facet to draw label-less points.
- RAW solve is not better than the JPG on the one frame tried; check a few more (defocused, wide) before recommending `auto`.
- `raw_xmp`: not run on real originals yet (dry-run on the TDS folder first); Lightroom/Capture One do not read `<image>.<ext>.xmp` for proprietary RAW.
- The crop offset is measured for the A7R IV only; other cameras fall back to the JPG unless sizes match.

### Astro session, quality metrics, culling (2026-11-11)
- **Architecture: PLUGIN_ARCHITECTURE.md Phase 0** (private notes `dev-notes\facet\PLUGIN_ARCHITECTURE.md` §4): astro-id as an HTTP sidecar (`astro-id facet sync|serve`, tags via `batch_manual_tags`, local ledger, annotations behind `--legacy-db` until PR-1, remove hardlink + atexit catch-up + Windows defaults). **Pending the owner's confirmation of D1-D6; not started.** The session/quality/cull code was built engine-side with no Facet access so it moves as is.
- Session page (Facet or sidecar UI) consumes `astro-id session` / `astro_id.session`; schema in CONTRACT.md v1.1.
- Quality on unsolved frames is not stored in Facet (no annotation row for a failed solve); a sidecar ledger or a minimal payload could hold it.
- Site precedence in the session: per-photo hint > session `--site`. Confirm with the owner (an explicit session site might be meant to override bad photo GPS).
- Twilight frames read high elongation (07624: 2.52) from few faint stars on a bright sky; consider weighting elongation by star SNR or skipping it below N stars.
- Grade thresholds (A: <= 6 px, B: <= 10 px) are tuned on one 61 MP camera at 14-20 mm; a per-lens or per-session grade may be better.
- Quality costs a second JPEG decode per frame (the solve decodes at half size separately); share the decode if speed matters.
- `best_fit_time` still comes from the Moon only (horizon pin is the open engine TODO above), so `suggest_tolerance_min` is null on Moon-less nights.


## Facet sidecar, Phase 0 (2026-11-11)
- [x] PLUGIN_ARCHITECTURE.md Phase 0 built (D1-D6 approved by the owner 2026-11-11). The "Architecture: Phase 0" item above is done.
- the owner's decisions before switching the live library: see README status gate.
- Annotations switch to `PUT /api/photo/annotations` by themselves when Facet PR-1 lands (feature-detected); then delete `astro_id/facet/legacy.py` (`--legacy-db`, `adopt`).
- Tags to `POST /api/photos/plugin_tags` when PR-3 lands (until then astro tags are user tags in exports and XMP).
- ~~Register in `plugins.tools` when PR-4 lands (link-out)~~ superseded 2026-11-11 by the add-on sidebar (PLUGIN_BOUNDARY.md v0.2), see "Facet add-on" below.
- Port `raw_xmp` (RAW XMP keywords via exiftool) from the removed plugin's git history if the owner still wants it.
- Quality of unsolved frames still has no home in Facet (no annotation row); the ledger or the serve page could hold it.
- A scheduled `facet sync --since last` (Task Scheduler, registered in scripts/persistent-tasks.md) if polling is wanted.
- serve: polling, no SSE; no cancel button for a running solve; the photo list holds up to 2000 photos.
- serve: add an overlay folder field (today a UI solve writes <folder>-annotated beside the photos).

## Facet add-on (2026-11-11)
- [x] (done; the add-on runs in :5003, gui-tested 2026-11-12..15) Install into the :5003 test copy once the Facet agent's viewer runs with `addons.enabled` (README status, step 1), then the owner's manual 1-6 (docs/TESTING.md).
- [ ] Firefox check of the sandboxed page (spec §12 b): only Chrome was tried.
- [ ] Delete the TEMPORARY env-file credential path in `addon_credentials()` once the fork mints add-on tokens.
- [ ] Switch tags to `POST /api/photos/plugin_tags` once its body is pinned in the spec (the probe is already there).
- [ ] Overlay folder field in the sidebar (a sidebar solve still writes `<folder>-annotated` beside the photos).
- [ ] Cancel button for a running solve; SSE instead of polling if Facet's proxy streams well.

### For the Facet side (the spec left these open; astro-id's assumptions in brackets)
1. **CORS preflight vs the nonce gate:** browsers send `OPTIONS` without `X-Addon-Nonce`. The proxy must answer preflights itself (ACAO `*`, allow headers `X-Addon-Nonce, Content-Type`, methods GET/POST) or exempt OPTIONS from the nonce gate; otherwise every panel fetch fails. [astro-id answers OPTIONS 204 behind the launch gate as well.]
2. **Stop on Windows:** `terminate()` on the spawned `uv.exe` does not end uv's children (trampoline, venv launcher, python). astro-id now exits on its own, but any other `uv run` add-on will orphan. Spec §5 calls a job object "a fork-only nicety"; for uv-launched add-ons it is needed (or `taskkill /T /F`).
3. **`plugin_tags` request body** (PR-3) is not in v0.2. [astro-id probes `POST /api/photos/plugin_tags` with `{}` and treats 422 as present.]
4. **Addon-token probe semantics:** a mapped, granted route must still answer 422 to an empty body (scope check before validation is fine); an unmapped or not-granted route answers 403. [astro-id: with an `fct_` token only 422 = usable.]
5. **`context.scope` shapes:** `run_id` = the viewer job id as a string matching `[A-Za-z0-9_.:-]{1,128}`; `view.query` as a query string (an object is accepted too); `selection` paths in `context.selection`, and when it is empty the panel falls back to `context.photo`.
6. **`GET /api/photos?scan_run=`** must combine with the usual filters (`category`) and the `hide_*=0` / `collapse_stacks=0` toggles astro-id always sends, and page with `has_more` like today.
7. **Host header:** the proxy and the supervisor's health call must send Host `127.0.0.1:<port>` or `localhost:<port>` (astro-id refuses a non-loopback Host, DNS-rebinding guard).
8. **`X-Forwarded-Prefix`:** exactly `/addons/<id>` (no trailing slash needed). astro-id uses only relative URLs, so it is informational.
9. **iframe src:** `/addons/astro-id/ui/?nonce=<n>` with the trailing slash after `ui`, so `api/...` resolves under `/ui/`. A `GET /ui` gets a 308 with the relative `Location: ui/`.
10. **Nonce charset:** astro-id accepts `[A-Za-z0-9_.~-]{8,256}` (`secrets.token_urlsafe` fits). **Launch secret:** >= 16 chars.
11. **stdout:** after the port line astro-id writes nothing to stdout (logging is stderr); uv's own warnings also go to stderr. The supervisor can read one line and then just drain.
12. **`/thumbnail`** must accept the add-on bearer under `photos.read` (spec table lists it); astro-id passes `size` 16..640.

## R-MCP + R-LOC (2026-11-11)
- [ ] the owner: Facet docs/TESTING.md T-ADDONS rows 14-19 (Google Earth pick, confirm, last site).
- [x] Annotations over MCP: `get/set/clear_annotations` (2026-11-12, Facet test branch `local/addons-annot` = feat/addons + PR-1 + the tools). Upstream: the tools go into PR-1, the allowlist line into feat/addons.
- [ ] Plugin tags (PR-3) instead of `add_tags` with `created_by` = astro-id.
- [ ] Thumbnails over MCP if Facet answers open question (g) with a resource; then drop the `GET /thumbnail` path.
- [ ] Facet version shows "unknown" (Facet's `status.server_version` is empty from a checkout; Facet BUGS B33).
- [ ] Maybe: keep one MCP session open per thread instead of one connection per call (3 requests per call today; fine for 18 photos, slow for thousands).
- [ ] Delete the TEMPORARY env-file credential path in `addon_credentials()` (Facet mints add-on tokens now).


## Folder or album, sky labels on (2026-11-12)
- [ ] the owner: Facet docs/TESTING.md T-ADDONS rows 14-16 (three broad checks; Claude ran the step-by-step flow in Chrome).
- [ ] Smart albums: Facet scopes `album_id` by fixed members only, so smart albums are left out of the picker. If wanted, Facet could resolve a smart album's saved filter server-side (a Facet change, not astro-id).
- [ ] An automated browser test of the panel (Playwright, against the stub Facet) so panel regressions are caught without a person.

## the owner's review 2026-11-12: panel restructure (approved; panel side built 6b7ad5c)
- [x] (panel side, 6b7ad5c; Facet side by the Facet agent) **Sky-label controls move into the Astro panel; Facet only draws.** Facet keeps the stored overlay + a drawing layer; the panel sends a bridge message (e.g. `annotation_view {groups, min_confidence, max_mag, show_scores, visible}`) and Facet's layer applies it. Drops PR-1's own filter panel and the label toolbar button. Starts hidden (the owner: right).
- [x] (6b7ad5c) **Two sections in the panel:** (A) *Setup for this folder / album*: photos, location, time offset, JPG/RAW; saved per folder AND per album. (B) *Run*: plate solve → review results in the panel → **Save to Facet** (tags + sky labels); culling later (low priority).
- [x] (6b7ad5c) Min-confidence leaves the solve step; it becomes a sky-label display setting.
- [x] (6b7ad5c) Terms, one each: **Tags** = searchable words on the photo (gallery filter). **Sky labels** = names drawn over the photo in Facet (stored data, live). **Labeled copies** = separate JPG files with the labels burned in, for sharing outside Facet (was "overlay images"; off by default now that Facet draws live).
- [ ] Label placement: near an edge the text goes inward (text then circle on the right edge, etc.), never past the image edge, no label over another label. Not hit on the TDS frames (one Messier at the edge). Needs per-label anchor side + a collision pass, in both the burned JPG and Facet's layer.
- [ ] Clock from the sky: plate solving does not use time; time only matters for the horizon and the Moon/planets. Two frames from a fixed tripod give the interval between them to ~1 s (the sky moves ~1 px/s at 20 mm), but the absolute clock needs a ground reference (horizon in frame) or the Moon/planets (minutes, not seconds). Possible: best-fit offset per folder to ±1-2 min from frames that show the horizon. Seconds give the labels nothing.

### Follow-ups from the restructure (6b7ad5c)
- [ ] GUI check of the new panel inside Facet :5003 once Facet's §6c side is in (another agent): Setup save for a folder and an album, solve -> review -> Save to Facet, sky-label controls on an open photo (Show/groups/sliders redraw live), culling still works.
- [x] (becf6a0) the owner: two sliders, separate: step 5 **Save names at confidence ≥** (default 65, kept with the setup) = what is saved as tags; step 6 **Show names at confidence ≥** = what is shown. Plus a **Help** page in the panel.
- [x] (becf6a0) the owner OK'd: "Apply tag" requires a Save first and only touches saved photos.
- [x] (becf6a0) the owner OK'd: a tick box per Results row; Save to Facet writes only the ticked photos.
- [x] (becf6a0) Small fixes: step 1 summary names the album; smart-album note hidden in Folder mode; Results says "N to save · M found" (was "98 found" vs the log's 12).
- [x] Panel width (the owner 2026-11-12): he controls it by dragging; start at Facet's default 380 (no code change; his stored 280 on :5003 reset to 380). Results reflowed for narrow widths in 8d4adfc (names under the row, file names ellipsized).
- [ ] Location Check wording: "18 keep theirs" when they already sit at that exact site; say "already at this site".
- [ ] Panel session lost on a full page reload (results gone); restore the last session from the server's job state.
- [ ] Known limit: the solve's group rule (a star in a tagged constellation is not tagged itself) uses the cut at solve time; saving at a much higher cut can leave such a star untagged when its constellation falls below the new cut. Re-solve at that cut if it matters.

## Panel redesign, priority-first (2026-11-12, the owner's feedback)
- [x] Top load bar: **Load what Facet shows** (album route, open photo's folder, ticked photos, import, folder filter of the opening view) labelled with the thing and its count (`api/count`), **Album** list loads on pick, **Import a new folder...** (paste path).
- [x] **Save setup** removed: setup saves itself on change and on solve (`api/save-config`, debounced 0.7 s); Export options are part of it (`export` key in `astro-id.json` / album setup).
- [x] Plate solve = one button, counter "Solving n / N · file" + bar; the solve never burns copies now.
- [x] **Export** (step 6): `POST api/export {paths, view, options}` renders from the last solve's payload or the one stored in Facet (`astro_id/export.py`, same filter as Facet's layer), background thread, `GET api/export` progress.
- [x] **Auto** time: `done.time_fit` (median `best_fit_delta_min` of solved frames, whole minutes, ± = spread + 2); one-click Use.
- [ ] the owner: docs/TESTING.md manual 1-3.
- [ ] Facet side (not astro-id): the live `context` has no browsed folder (`routeOf` strips `?path_prefix=` / `?prefix=`). Adding `path_prefix` to the context (the `PanelScope` type already allows it on the opening scope) would let "Load what Facet shows" offer the folder being browsed, not only the open photo's folder. The panel already reads `scope.path_prefix` when present.
- [ ] Auto time from planets / the horizon: the engine pins the time only from the Moon (`moon_best_fit_time`); planets move too slowly against the stars to pin minutes, the horizon needs the foreground-boundary detector (Engine TODO). Until then Auto says so instead of guessing.
- [ ] Export legend comes from the payload's legend line (time ± tolerance, solver, grade, stars), not the file name; the old solve-time copies also had the file name and rms.
- [ ] Export label placement is the old overlay's (5 candidate spots, skip on collision); the edge-aware placement item above applies here too.
- [ ] Panel session still lost on a full page reload (results gone; Export then falls back to the sky labels stored in Facet).
- [x] GUI-test fixes: `PAGE_VERSION` in serve.py = the page's constant; a mismatch or an unknown api route shows "Astro was updated: stop and reopen it from Facet's Add-ons page." (bump both together on every serve.py change the page relies on). Accent buttons use dark text in dark mode (white on pale blue read as disabled). Counter names the frame being solved. A photo opened from an album offers the album viewed last when the photo is in it (`api/count {album_id, path}` -> `contains`), else its folder, with a note.
- [ ] Facet side (not astro-id): a photo opened from an album arrives as `/photo?path=` with `album_id: null`.

## Model D (2026-11-12, branch model-d 6ed2e11)
- [x] Summary plugin tags, indexed names, `set_annotations_batch`, one MCP session per job, rate-limit retry, migration of old per-object tags, old-Facet fallback, summary read for sky-label controls, `network` in the manifest. Tests: pytest 267 (12 new in `tests/test_facet_model_d.py`), node 16/16.
- [ ] the owner: docs/TESTING.md "Model D" checks 1-3, against fix/review-all on a test data copy.
- [x] (merged into main 43f652c, 2026-11-12) Merge `model-d` into master after the owner's check (master is served live to the GUI test; not touched).
- [ ] Re-install the manifest on the model D Facet (adds `network`); keep `--older-facet` for local/addons-annot.
- [ ] Id: Facet recommends `raysalem.astro-id`. Renaming orphans the tags/annotations written under `astro-id` (Facet's "Data left behind"); only with a planned purge + re-save. Kept as is.
- [ ] Option: index group members (a star covered by its constellation) too; cheap with the 200 cap, but differs from the old tag rule. Not done.
- [ ] Phase 4 re-reads every photo (`get_photo`) to confirm tags: 2 reads per photo per save; fine at 600 reads/min up to ~250 photos a minute. Could trust `add_tags` counts instead.
- [ ] Facet `status` has no dedicated feature name for plugin tags / detections; astro-id keys on `features` being present. If Facet adds e.g. `plugin_tags` / `detections`, check those instead. Passing the album (e.g. `?album=<id>` on the photo route, or `album_id` kept in the context) would replace the "album viewed last" guess.

## Review fixes (2026-11-12, master 2ff5948, 94f60e4, fda6c53)
- [x] Findings 1-10 of REVIEW.md fixed with regression tests (pytest 283, node bridge 20 + panel 4).
- [ ] the owner: docs/TESTING.md panel checks for the new behavior (save line "old tag(s) are yours to remove", searchable count matches the saved count, stale banner disables the buttons, no duplicated solve log lines). Not driven in a browser by this session.
- [ ] Legacy tags (ledger `legacy_tags`): no tool removes them yet; a one-off "remove with the user path" action in the CLI would close it. Panel only counts them.
- [ ] `status_retry_s` (30 s) and the 60 s version poll are untuned guesses.
- [ ] Re-install the add-on manifest is not needed; PAGE_VERSION is 2026-11-12.6.

## Load status line (2026-11-14)
- [ ] the owner: in Facet, load an album, move to another album, to the Albums list, back: the top line should follow (see USER_GUIDE "Is an album loaded?"). Not driven in a browser by this session.
- [ ] PAGE_VERSION is now 2026-11-14.1 (supersedes 2026-11-12.6 above); the add-on process must restart once to report it, else the panel shows the stale banner.

## Self-update (2026-11-14, branch self-restart 6d8762e, B-P8)
- [x] Keeper + worker, `api/restart`, session handoff, panel update states; pytest 296, node bridge 28 + panel 17; real restart under uv checked (1.4 s, 0 processes left after Stop).
- [x] (merged into main 91b1b24, 2026-11-14/15; `object-tags-wording` merged too, 2c46bcd) Merge `self-restart` (worktree <local path>, local, unpushed) into main; then merge `object-tags-wording` (its index.html will conflict on PAGE_VERSION and the update block: keep 2026-11-14.2 and this branch's update code).
- [ ] the owner, once: the running add-on predates the keeper, so after the merge click Stop, then Open on Facet's Add-ons page (the panel tells you). From then on no clicks.
- [ ] the owner: check in Facet: open the panel, change any astro-id .py file (or pull a commit), wait up to 60 s or reopen the panel: "Updating Astro…" then the same panel works; start a solve first to see "Astro will update after this solve."
- [ ] Dependency changes (pyproject) are not synced by a self-update (the worker uses the venv as is); if one breaks the import, the panel falls back to Stop/Open, which runs `uv run` and syncs. Could run `uv sync` from the keeper first if this happens often.
- [ ] Facet side (recommended, not needed now): a supervisor `restart` that keeps the panel nonces, or a bridge message `restart_addon`, would let any add-on update without a keeper. See BUGS B-P8 step 4.

## Object-tags wording (2026-11-14)
- [ ] the owner: read the panel and Help once. Branch `object-tags-wording` (worktree <local path>) is local, unpushed, not merged; merge after the self-restart branch. PAGE_VERSION not bumped (page wording only).
- [ ] Older sections of USER_GUIDE/FEATURES (before 2026-11-14) still say searchable names; left as history.

## Panel (from B-P9, 2026-11-15)
- A reopened/reloaded panel now restores the held photo set ("Loaded: ... ✓"), but not the last solve's results (step 4) the add-on still holds (also through a self-update handoff). Replay api/job?after=0 on restore when `update.unsaved` or the job has results.
- [x] Keeper: smoke-test a new worker (api/state answers) before retiring the old one; a half-edited checkout took the panel down 2026-11-15. Done on branch `safe-update` (B-P10, see Safe self-update below).
- Work in a worktree (e.g. <local path>, branch status-line) while the owner tests from the live checkout; fast-forward main only when green.

## Safe self-update (2026-11-15, branch safe-update, B-P10)
- [x] Settle 10 s + probe (trial run on a private port) before an update is offered; one verdict per code fingerprint; keeper checks the new worker after the switch and falls back to answering itself; panel lines for not-ready / did-not-start; 45 s text made accurate. pytest 321 passed (+22 in tests/test_safe_update.py), node bridge 31 (+1), panel 26 (+5). PAGE_VERSION 2026-11-15.2.
- [x] (merged into main 551c785, 2026-11-15) Merge `safe-update` (worktree <local path>, local, unpushed) into main when the owner is not mid-test (coordinator merges).
- [ ] the owner, once after the merge: the panel updates itself onto this code (the running worker probes nothing yet, so this one update is unguarded, as before). For the keeper-side check and fallback, the keeper itself must be new: one Stop, then Open on Facet's Add-ons page at a convenient time.
- [ ] the owner: check in Facet: with the panel open, add a syntax error to any astro-id .py file in a scratch way (or ask an agent to), wait ~15 s, reopen the panel: "An update to Astro is not ready yet; still running the previous version.", buttons work; undo the error: the line goes and the panel updates by itself within ~1 min.
- [ ] Not covered: index.html/bridge.js edits (Facet reads them per load; not in the fingerprint), code that answers api/state but is wrong elsewhere, pyproject dependency changes (no uv sync).

## Public repo (2026-11-15)
- [x] Public repo created: https://github.com/raysalem/astro-id (branch main pushed, full history, MIT). the owner asked for it.
- [ ] README says `git clone <this repo>`: put the real URL.
- [ ] Panel placeholder shows a private NAS path as the example folder: replace with a neutral example.
- [ ] Third-party notices: check the README lists the licenses of the data it downloads (OpenNGC CC-BY-SA, Stellarium constellation lines GPL-2, Hipparcos, DE421) before announcing the repo.
- [ ] Work branches still local only (safe-update, p3-real-contract): push to the public repo when merged into main.

## Panel undo (2026-11-15, branch panel-undo)
- [ ] the owner: test U1-U3 (docs/TESTING.md "Panel undo"), then merge `panel-undo` into main (PAGE_VERSION will conflict with the sibling branches: take a fresh one, both files together).
- [ ] CLI `astro-id facet undo` still uses the older `remove_all` (per-tag deletes, no model D adopt/ownership report). Could call `sync.undo` too.
- [ ] Older Facet limit: a tag the user typed AFTER astro-id saved the same word is one row; undo removes it (Facet does not record two owners for one row). Model D has no such case (object tags are detections).

## Public repo polish (2026-11-15) - branch `integration`, NOT merged into main
- [x] `integration` = main + version-check, cli-gaps, site-defaults, simple-label, panel-undo, sky-labels-clarity, readme, manual, examples, demo-video, ci. PAGE_VERSION 2026-11-15.3. Local: 376 pytest, 60 JS. Cloud CI green: https://github.com/raysalem/astro-id/actions/runs/43738260938
- [ ] the owner: look at https://github.com/raysalem/astro-id/tree/integration (does the demo play? pitch line? photo license wording "All rights reserved"?), then say merge.
- [x] small-photo-solve merged into `integration` (0c9af9e; docs moved to REFERENCE "How it solves" + README Limits line + MANUAL troubleshooting). Examples rebuilt at one size, 2400 px, all grade A (19a421e): frame 03 is now DSC07620 "dawn-orion-sirius" (replaced Taurus/Perseus/Uranus); 05 went grade C -> A (Jupiter/Mars 100). expected.json carries `scores`; demo captions show exact confidences (M45 99), legend covered, dawn shot zoomed; demo.gif (640 px, 7.5 s, 2.5 MB) inline in README, demo.webp/poster removed. Rebuild: tools/make_examples.py (steps in docs/TESTING.md). Local 420 pytest, 60 JS. Cloud CI green: https://github.com/raysalem/astro-id/actions/runs/43740733477
- [ ] Label placement: names overlap (URSA MINOR / Little Dipper, CASSIOPEIA / W of Cassiopeia) - visible on the public examples.
- [x] `astro-id label` now uses require_astap() like `solve` (9721576; test covers missing, too old, fallback, --no-fallback).
- [x] gallery-4/5 removed (strip = 3 non-hero frames, frame 02 linked); README 212 lines.
- [ ] Quality metrics on twilight frames: 03 grades quality C (39 stars) and 05 quality C ("1 stars, HFD None") although both plate-solve at grade A with 900-1500 matched stars; a cull rule would reject them.
- [ ] After merge into main: the owner's live add-on picks up the panel changes (Undo, Sky labels switch, requirements line) by itself; the other session's branches (safe-update, p3-real-contract) must be rebased on the new main.
- [ ] Optional: simple drag-and-drop app on top of `astro-id label` (2-3 days; bundling ASTAP is the hard part).

## Solve across photos to finalize time and location (the owner, 2026-11-15)
- [ ] Today each photo is solved alone and time/location may be fuzzy (an approximate site and a clock offset with a tolerance still solve; they only affect planets, the Moon and the horizon). TODO: use a whole set together to pin both down: (a) time - the planets/Moon positions and the sky's rotation between frames from a fixed tripod give the clock offset to about a minute or better; (b) location - the horizon altitude of stars at a known time, and the Moon's parallax, bound latitude/longitude. Output: one best-fit offset and site for the folder/album with an error bar, offered as "Use" like today's Auto time. Related: the older "clock from the sky" note above.
  - Input already available (B-E2, branch horizon-time-fix): each solved frame with a site carries `time_hint` (`sky_below_horizon`) when its own matched stars were below the horizon at the time used, and `identify.sky_check()` gives the share of matched stars below -2 deg for any candidate time. That is a hard constraint for the fit: the true offset must put every frame's matched stars above the horizon (and the lowest matched star near the frame's bottom edge bounds it further). Scanning offsets in 1 h steps across a day narrows a fully unknown clock to the hours where the sky was up, before the Moon/planets fine-tune minutes. Today `time_fit`/Auto only counts these frames (`sky_below`) and the panel says the clock is wrong; it suggests nothing from them.

## Moon geography (the owner, 2026-11-15) - side item, not started
- [ ] "Plate" the Moon itself: on a zoomed-in Moon photo, name its geography (maria, major craters, mountain ranges, landing sites) with a confidence, the way sky photos get object names. Needs: a Moon close-up (long lens / crop where the disc is hundreds of pixels across); finding the disc and its limb; orientation and libration for the photo's time and place (ephemeris); mapping a lunar feature catalog (IAU/USGS Gazetteer of Planetary Nomenclature) onto the disc; labels + object tags ("mare imbrium", "tycho", "copernicus"). The terminator hides part of the disc: only label lit features. the owner can share example photos.

## Phone use (the owner, 2026-11-15)
- Decision: Astro is not recommended on a phone for now; the panel needs too much space. Keep the side drawer as is at phone width; no bottom sheet work. Say so in Help / USER_GUIDE ("use a desktop-width window") next time the panel text is touched.
