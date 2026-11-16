# astro-id: features

*Moved from the maintainer's private working notes on 2026-11-15 and scrubbed for this public repo. Sections are dated; later sections replace earlier ones where they say so.*


## Engine
- `astro-id solve <file-or-folder>`: plate-solves night-sky photos (ASTAP + W08 on a central ~30 deg crop, then a full-frame TAN-SIP refit to Hipparcos that grows ring by ring into the corners).
- Identifies Messier + bright NGC/IC (OpenNGC), IAU-named stars to mag 4.5, the 88 constellations (Stellarium modern stick figures), Sun/Moon/planets (DE421, topocentric).
- 0-100 confidence per object; tiers high >= 80, likely >= 65, candidate < 65; every object is in the JSON, consumers filter.
- Clock correction: per-folder `astro-id.json` or `--offset`; time tolerance (default 15 min) evaluated worst-case; Moon-based best-fit time + `time_flag`.
- Overlay `<stem>_astro.jpg` (3000 px): constellation lines + names, tier-colored circles and `name score` labels, legend with time +/- tolerance and solve grade.
- `astro-id-report.csv` per folder (one row per object, with worst-case altitude and planet-to-source separation).
- `astro-id site <folder>`: Google Maps pin -> lat/lon -> USGS elevation -> name -> confirm -> `astro-id.json`.
- `astro-id setup`: downloads reference data, reports ASTAP + databases.
- astrometry.net fallback only when ASTAP fails and `ASTROMETRY_API_KEY` is set (private upload).
- `astro-id label <photo-or-folder>`: the one-step labeler behind `astro-label.cmd` / `astro-label.sh` ([SIMPLE](SIMPLE.md)).
- `astro-id check` (alias `doctor`): minimum requirements (Python, uv, ASTAP + database, optionally a Facet), exit 0 when all required ones pass; the Facet panel shows the same rules as one line when something is missing.

## Moon close-ups (2026-11-15, first version)
- `astro-id moon <photo-or-folder> [--time] [--site] [--north-angle] [--mirror] [--to] [--json]`: fits the disc, reads the phase or an eclipse, finds the orientation (given angle, time + site with a level camera, or a maria fit), and names maria, large craters, mountain ranges and Apollo sites on the lit part, 0-100 each, in `<name>_moon.jpg`.
- Limits: craters need a disc of about 250 px, maria about 60 px; not wired into `solve`, `label` or the Facet add-on yet. Method, measurements and what is left: [the Moon plan](plans/2026-11-15-moon-features.md); examples: [examples/moon](../examples/README.md#moon-photos-moon).

## Facet plugin
- `facet_plugin/astro_id.py`: Facet plugin, `on_score_complete` queues astro photos (category astro, astro tags, or under `roots`) to one background worker that runs the engine and writes tags.
- Tags to `photo_manual_tags` (`source='user'`, `created_by='astro-id'`): `plate-solved` / `plate-solve failed` + objects >= min-confidence. Idempotent replace per photo; other sources never touched; 50-tag cap respected.
- Backfill CLI with `--dry-run`, `--min-confidence`, `--db`.

### Facet as source of truth (2026-11-09)
- Facet is the source of truth for location and time: per photo GPS (manual or EXIF) becomes `--site`, a manually set capture date becomes `--time` (`time_source: "facet"`). Engine: `--time`/`--timezone`, USGS elevation for `--site lat,lon`, timezone inferred from the site (also fixes `astro-id site` writing `timezone: null`).

### Tag hierarchy + plugin-controlled runs (2026-11-10)
- Engine tag policy: every object has `level` (1 constellation, 2 Sun/Moon/planet, 3 Messier/NGC/IC, 4 asterism, 5 star), `magnitude`, `parent` (IAU constellation), `member_of` (cluster/asterism) and `taggable`. Defaults: levels 1-4 on, stars off (`--star-mag-limit 1.5` turns the brightest on), `--max-catalog-mag 6.0`, members never tagged next to their group (Pleiades stars under `m45 pleiades`, belt stars under `orion's belt`). `--tag-style kind` gives `constellation/orion`, `planet/jupiter`, `messier/m45 pleiades`.
- 13 asterisms as HIP-id lists written for this repo (Orion's Belt, Big/Little Dipper, Summer/Winter/Spring Triangle, Winter Hexagon, Great Square, Sickle, Teapot, Northern Cross, Keystone, W of Cassiopeia); found when >= 80 % of members are in frame and detected; pink stick figures on the overlay.
- Stacking sequences: consecutive frames (gap <= 120 s, same lens/focal/exposure/size) solve the middle frame once; the others register by star pattern and reuse its objects (`solver: "stack-copy"`, `stack_of`) when the pointing moved < 2 % of the diagonal, else solve alone. `--stack-mode all` turns it off.
- CSV gains level/magnitude/parent/member_of/taggable/stack_of; overlay marks subsumed member stars small and unlabeled.
- Plugin: filters `taggable && confidence >= min`, caps in level order (status, 1..5, confidence), passes the policy/stack config keys as flags.
- Plugin: exit hook in `facet.py` scan processes spawns a detached `--pending` catch-up (lock file, log `<local app data>`), so multi-pass scans get tags without a Facet code change. `--pending [--since]` backfill mode.

### Annotations, RAW source, plugin config (2026-11-10)
- Engine result gains `annotations` (ANNOTATIONS.md v1): every object at every level (stars to mag 6.5 with magnitude), constellation/asterism stick figures as `lines` segments sampled every ~1.5 deg of sky, SIP-projected, clipped to the frame, 0.1 px, `member_of` = slug id of the group item, legend with time +/- tolerance, solver, grade, stars; capped at ~500 KB. Frame result also gains `solve_source`.
- Overlay JPG labels are plain names; `--overlay-scores` (plugin `overlay_scores`) adds the confidence. Legend keeps the tier colour key.
- `--solve-source jpg|raw|auto` (also `astro-id.json`, plugin `solve_source`): solve the RAW (linear demosaic, cropped to the JPG frame), coordinates stay in JPG space.
- Plugin: upserts the payload into Facet `photo_annotations` (source `astro-id`) in the tag transaction via `db.annotations.upsert_annotation`; skips (logs once) if the helper or table is missing; `plate-solve failed` deletes the row.
- Plugin: `tag_targets` (`facet`, optional `raw_xmp` = keywords into the RAW sidecar `<image><ext>.xmp`, exiftool >= 13.28, merge-only, tracked in a private XMP namespace bag; default off).
- Plugin: config validated on load (unknown key = warning, bad value = error + plugin disabled). Consolidated option table in README.md "All options".

### Astro session, quality metrics, culling (2026-11-11)
- Engine frame result gains `quality` (CONTRACT.md v1.1): `detected`, `matched`, `measured`, `detect_scale`, `fwhm_px` (median half-flux diameter, full-res px), `fwhm_arcsec`, `elongation`, `roundness`, `background`, `noise`, `grade` A/B/C, `measured_on`. On solved, unsolved and stack-copy frames. `--no-quality` skips it.
- Frame result gains `best_fit_delta_min`; `--timezone` now applies to EXIF + offset too.
- `astro_id/cull.py`: `cull_rank(metrics, rule, reference, solved)` -> keep/review/reject + reasons; `cull_session` with a session baseline (best-focus quartile, median star count). Rule `{"enabled", "order", "thresholds"}` from `--cull-rule`, `astro-id.json` `"cull"`, plugin `astro_id.cull` (validated). Defocus never rejects.
- `astro_id/session.py` + `astro-id session`: `--preview` (EXIF time, corrected time, window, site/tz source, RAW twin, warnings), solve with `--offset --tolerance-min --source --site --tz --min-confidence`, per-photo `hints` (site/time a library holds), `--save-folder-config`, JSON-lines events start/config_saved/photo/done; done carries the cull verdicts and a suggested tighter tolerance. `astro-id cull` re-runs only the culling.
- Plugin (minimal): `trigger` `"manual"` (default: scans never solve) | `"scan"` (old behaviour); `cull` key validated; `quality` stored as a top-level object in the `photo_annotations` payload. Personal paths removed from plugin defaults/docs/tests.


## Facet sidecar, Phase 0 (2026-11-11) - supersedes the "Facet plugin" sections above
- `astro-id facet sync`: candidates by `--category` (default astro, empty = any), `--path-prefix`, `--tag`, `--album`, `--since YYYY-MM-DD|last`, `--force`, `--limit`, `--spool`; every frame (gallery hide toggles off). Hints from Facet: GPS as site, capture date as true time only when corrected in Facet. Solves in process via `astro_id.session.run_session` (session flags `--offset --tolerance-min --tz --source --site --overlay-dir --stack-mode --engine-flag`). Writes tags (`plate-solved`/`plate-solve failed` + taggable objects by level, Facet's 50-tag cap with other tags counted first, AI-tag duplicates skipped) and the annotation payload with `quality`. `--dry-run`, `--json` (session events + one `written` per photo + `sync_done`).
- Ledger per Facet URL + photo: tags astro-id added, payload hash, how stored (api / legacy-db), status, Facet version; `--since last` watermark. Re-runs remove only ledger tags; unchanged results write nothing; a tag the user owns is never claimed or removed; a planned tag Facet did not store never enters the ledger.
- Annotations: `PUT /api/photo/annotations` feature-detected (a probe that cannot write); else `--legacy-db PATH --facet-dir DIR` (TEMPORARY, Facet's own validator/upsert loaded by file path); else skipped with a warning. Unsolved frame: our row removed.
- `astro-id facet serve`: 127.0.0.1, random secret path, Host/Origin checks, stdlib server + one HTML page. Steps: photos (prefix/album/tag/category) -> location (GPS source per photo; paste lat,lon or a Maps URL -> `PUT /api/photo/gps`; link to the photo page in Facet) -> time (`time_preview`: camera vs corrected time, window, sources; save offset/tolerance/tz to the folder astro-id.json) -> plate solve (background thread, polled JSON-lines events, then the sync writer) -> culling (quality table, keep/review/reject + reasons, rule editor: enabled, order, thresholds; re-cull without re-solve). Optional `astro-review` ledger tag for review/reject frames, off by default. Facet's reject/cull flags are never written.
- `astro-id facet status` (ledger dump), `undo` (remove ledger tags + our annotations), `adopt` (TEMPORARY one-time: read the old plugin's `created_by='astro-id'` tags and annotation rows, read-only, into the ledger).
- Optional `facet_plugin/astro_notify.py.example` (under 70 lines): appends scored astro paths to a spool file; never touches the DB.
- Credentials from env: `FACET_EDITION_PASSWORD` | `FACET_USERNAME` + `FACET_PASSWORD` | `FACET_TOKEN`; `FACET_URL`. One login per run (Facet rate-limits logins: 429). No stats_cache writes.
- Removed: `facet_plugin/astro_id.py` (kept in git history), its hardlink in the Facet test checkout plugins folder + the `.git/info/exclude` line, the atexit catch-up + lock file, `DEFAULT_UV` and other Windows-only defaults, the `plugins.astro_id` block in Facet's config. Dropped with it, not ported yet: `tag_targets: raw_xmp` (RAW XMP keywords), see TODO.

## Facet add-on (2026-11-11)
- `astro-id facet install --facet-data <dir>` / `uninstall`: writes or deletes `<dir>/addons/astro-id/addon.json` and nothing else (spec §4). `start.argv` holds the absolute `uv` (from `UV`, PATH or `~/.local/bin`) and the checkout path; `--uv` / `--project` override.
- `astro-id facet serve --addon` (spec §5): config from Facet's env only, one stdout line `FACET_ADDON_PORT=<n>`, `GET /health?c=` HMAC challenge/response, `X-Facet-Launch` gate on everything else, path refusal, page + API under `/ui/`, no cookies.
- Exits by itself when uv (the process Facet started) or Facet ends, so Stop or a Facet exit never leaves an orphan on Windows.
- Step 1 from Facet's scope: import run (`scan_run`; category `astro` by default, editable), current view (gallery query), or selection (<= 500). The manual folder/album/tag loader stays. A later scope change offers "Load the new photos" instead of wiping a session.
- Sidebar layout: 5 collapsing steps sized for ~380-480 px, a status per step in its header, detail on hover, a thumbnail per photo fetched by the add-on backend.
- Bridge v1 (spec §8): `ready` on load; `context` only from `window.parent` with the nonce; `show_photos` (Show in Facet, Show keep/review/reject), `open_photo` (photo names), `refresh` after GPS, solve writes and the review tag.
- Writes: tags via manual tags + ledger (plugin_tags is detected and noted, not used until its body is pinned), annotations via PUT when granted (else a sidebar note), GPS via `PUT /api/photo/gps`. Never cull/reject flags.
- Credential: `addon_credentials()`. `FACET_TOKEN` first; a TEMPORARY env-file password otherwise (the sidebar says so); else read-only.

## R-MCP + R-LOC (2026-11-11)
- Talks to Facet over Facet's MCP endpoint with its add-on token (only the tools Facet's allowlist grants it).
- Location per folder or album: Google Earth / Maps picker, decimal or DMS with elevation, one confirm, last site remembered, saved as the folder site for plate solving.


## Step 1: folder or album; sky labels in Facet (2026-11-12)
- Step 1 "Photos": pick **Folder** (paste a path) or **Album** (a list of Facet's regular albums). Location, time offset and saved settings work the same for both.
- With a Facet that has the annotations PR, a solve stores each frame's sky labels in Facet over MCP (`set_annotations`); the photo page draws them live with a show/hide button and filters.

## Panel restructure: Setup / Run, Save to Facet, sky-label view (2026-11-12, 6b7ad5c)
- **A. Setup for this folder or album**: 1 Photos (Folder | Album, Solve from JPG/RAW), 2 Location, 3 Time offset, then one **Save setup**: every folder's `astro-id.json` and, when loaded from an album, the album's own setup. Loading an album pre-fills from its album setup first, else from its folders' `astro-id.json`.
- Album setup store: the ledger's `meta` table (SQLite in the add-on data dir), key `album_setup:<album id>`, scoped by Facet library; JSON `{clock_offset, time_tolerance_min, timezone, solve_source, labeled_copies, sky_labels}`. Merged on save (an unset value keeps the stored one).
- **B. Run**: 4 Plate solve (labeled-copies options here) writes nothing to Facet; 5 Results: per photo thumbnail, solved?, grade, objects found (name + confidence, expandable), then **Save to Facet** (tags + sky labels; safe to repeat, the ledger makes a re-save a no-op); 6 Sky labels; 7 Culling (low priority, collapsed).
- **Save cut + ticks + Help (becf6a0)**: step 5 "Save names at confidence ≥" (0-100, step 5, default 65) is sent as `min_confidence` to `api/save` (overrides the solve's cut for the tags) and saved as `min_confidence` in `astro-id.json` / the album setup; `folder_config` returns it. Results rows have tick boxes (failed unticked); `api/save` gets `paths`. `api/review-tags` refuses (400) before a Save and covers only `Job.saved_paths`. Header **Help** swaps `<main>` for a help section (`[data-help]` links jump to an anchor).
- **Sky labels** (step 6): shown when the photo open in Facet (`context.photo`) has astro-id sky labels. Show sky labels (starts off), the payload's groups, Min confidence (payload score), the payload's metrics (Max magnitude), Show scores. Every change and every new context posts `annotation_view` (PLUGIN_BOUNDARY §6c). The view, minus Show, is saved with the setup (`sky_labels` in `astro-id.json` / album setup).
- Routes: `POST api/save` (`{paths?}` -> reports + counts), `GET api/annotations?path=` (payload groups / metrics / score, no geometry), `api/job` adds `saved` and `done.review`; `api/save-config` takes `album_id`, `labeled_copies`, `sky_labels`; `api/photos` with `album_id` returns `album_config`.
- Bridge: `annotationView(view)` posts `{type: "annotation_view", v, nonce, view}`; `sanitizeView` keeps only the contract's keys and types, `visible` false unless true.
- Terms everywhere: **tags**, **sky labels**, **labeled copies** (was "overlay images"). Labeled copies are off by default. An older `astro-id.json` `overlay: {on, lines, scores}` is read as labeled copies and replaced by `labeled_copies` on the next save.

## Panel redesign: priority-first workflow (2026-11-12, the owner's feedback)
Design rule (the owner): every panel starts from "what is the user's priority here"; rank, show the top, fold the rest toward the bottom. Clutter is a defect.

Workflow (the owner): pick folder -> location -> time (with Auto) -> see it with sky labels (review + save tags) -> export (pick details at export). Culling last, folded.

| Section | Priority | Why | Default |
|---|---|---|---|
| Load bar: **Load what Facet shows** (folder of the open photo / album being viewed / selection / import) · **Album** list · **Import a new folder...** (paste path) | 1 | Nothing works without photos, and Facet already knows what the user is looking at: one click, labelled with the thing ("Load folder astro (18 photos)") | Always visible, top |
| Loaded line: what is loaded, saved setup summary, "Photos (18)" list + JPG/RAW | 2 | Confirms what you are working on; the per-photo list is detail | Line visible; list folded |
| 1. Location | 3 (1 when missing) | Planets, Moon and horizon need it; once saved it is done | Open when the folder/album has no saved site; else folded with the site in its header |
| 2. Time (offset, ±, timezone, **Auto** after a solve) | 3 (1 when missing) | Same: once per folder; Auto only after a solve and only from frames that pin the time (today: the Moon) | Open when no offset saved; folded otherwise; opens itself when Auto has a suggestion that differs |
| 3. Plate solve: one button, counter "Solving 3 / 18 · IMG_0012.JPG" + bar, log folded | 1 once setup is ready | The job itself; no options to decide | Open when the setup is ready |
| 4. Results and tags: rows with ticks, "Save names at confidence ≥", **Save to Facet** | 1 after a solve | Review what was found, then write tags + sky labels | Opens when a solve finishes |
| 5. Sky labels on the photo (the annotator: Show, groups, show-names confidence, max magnitude, scores) | 2 after a save | "See it": what Facet draws, and what Export burns | Opens when the photo open in Facet has sky labels |
| 6. Export labeled copies (plain-language options at export time) | 3 | Sharing outside Facet: occasional, last | Folded; status in header |
| Culling | 5 | Advisory, low priority (the owner) | Folded, greyed header |
| Notes / log | 6 | Diagnostics | Folded |

Removed from the user's view: **Save setup**.

### Load status line (2026-11-14, the owner's test feedback, page 2026-11-14.1)
Top of the panel, always visible, exactly one of: **Nothing loaded yet.** + `Load <Album X> (N photos)`; **Loaded: <Album X> . N photos** with a tick when it is what Facet shows (small Reload link); **Loaded: ... . N photos** + **Facet is showing: <Album Y> (M photos)** + `Load <Album Y> instead`. When Facet shows nothing loadable (Albums list, Add-ons): "Open an album, a folder or a photo in Facet, or pick one here:" above Album / Import. Recomputed on every bridge context update; never auto-loads and never touches a session in progress (import and ticked-photo scopes still auto-load into an empty panel only). Setup / "already saved" lines sit under it, headed "About the loaded set (...)". The last-viewed-album memory is dropped when the context has neither an album nor a photo.

### Self-update (2026-11-14, branch self-restart 6d8762e, page 2026-11-14.2, B-P8)
The add-on brings itself up to date; Stop/Open is no longer needed after a code change.
- **Keeper + worker** (`astro_id/facet/keeper.py`): the process Facet starts binds the socket, prints `FACET_ADDON_PORT=<n>`, and runs the server as a child worker that serves on the same socket. Same port, same launch secret, same Facet token for every worker, so Facet never notices a restart and the panel's nonce stays valid.
- **When**: the panel restarts the add-on when its Python code changed on disk since it started (`api/state.update.code_changed`) or the page is newer than it; checked when the panel opens and every 60 s.
- **Session kept**: loaded photos, the last solve (saved or not), the last export travel through a handoff file to the new worker.
- **Never cuts work**: refused while a solve or export runs; the panel says "Astro will update after this solve/export" and asks again every 3 s.
- **Panel older than the code**: reloads itself after the restart (nonce kept); if a finished solve is unsaved it first says "Astro will update after you save these results to Facet".
- **Fallback**: an add-on too old to restart itself, or one that does not come back within 45 s: "Astro needs one restart to update: on Facet's Add-ons page click Stop, then Open."
- Restart time measured: 1.4 s under uv with the fake Facet.

### Safe self-update (2026-11-15, branch safe-update, page 2026-11-15.2, B-P10)
A working add-on is never replaced by code that cannot serve.
- **Settle**: new Python code is only considered after its files have been unchanged for 10 s (a multi-file edit or a git checkout is not picked up half-way). `api/state.update.pending` is true meanwhile; the panel shows nothing and asks again every 3 s.
- **Trial run (probe)**: the running worker starts the new code once as a throwaway process on a private port and checks it answers `/health` and `api/state` within 20 s. Only then is `update.code_changed` true and `api/restart` accepted.
- **Failed trial**: `update.update_failed` = {reason, at}; the old version keeps serving, nothing is locked; the panel shows one line, "An update to Astro is not ready yet; still running the previous version.", and asks again every 10 s. The same files are never tried twice; any change to them is tried again after it settles.
- **After the switch**: the keeper checks the new worker answers api/state (20 s) and stays up (30 s). If not, the keeper answers by itself on the same port (`update.fallback`): "An update to Astro did not start; Astro will try again as soon as its files change." Other requests get 503 with that sentence. The loaded photos and unsaved results are kept for the next worker, which starts as soon as fixed files pass a trial run.
- **45 s fallback**: now only for an add-on started before this keeper: "Astro did not come back after updating: on Facet's Add-ons page click Stop, then Open."

## Model D: summary tags + searchable names (2026-11-12, branch model-d, 6ed2e11)
*History wording: since 2026-11-14 the panel and Help say **object tags** where this section says "searchable names"; merged into main 43f652c. Not rewritten here on purpose (it records what was built then).*
Facet side: fix/review-all (merge 494539802), PLUGIN_MCP_REVIEW §3 tag model D. Branch `model-d` in worktree `<local path>`.
- **Detection**: `McpFacetClient.features()` reads `status`; `features` list containing `annotations` = model D (`model_d`, `plugin_tags`, `annotations_batch`, `annotation_summary`). Also read from `tools/list` (`add_tags` takes `plugin` and `set_annotations_batch` exists), so a failed `status` does not decide it; a guess is re-asked before use. Model D tag mode needs sky labels on and a token that can store them, else per-object tags as before. An older Facet (local/addons-annot) reports none: per-object tags exactly as before. The `/api` password client is never model D.
- **Tags**: at most 3 summary tags per photo (`plate-solved` / `plate-solve failed`, `astro-review`), `add_tags {plugin: true}`; no per-object tags.
- **Searchable names**: one payload per photo; items whose name is saved (confidence ≥ the step 4 slider and `taggable`) carry `index: true`; only true is written (absent = not indexed, no `index_groups`), so the payload stays under Facet's 512 KB. Facet answers `indexed` / `index_skipped`.
- **Members covered by their group** (a star in Orion's Belt, a star in a cluster): NOT indexed, same rule the tags had; the group name is searchable and the member stays a sky label. Stars are off by default anyway.
- **Batch + one session**: overlays through `set_annotations_batch` (≤200 photos, <9 MB per call); tag adds/removes one call per tag with all its photos; `apply_results` / `remove_all` run inside `client.job()` = one MCP `initialize` per save. `rate_limited` retried after `retry_after_s` (≤3 times, ≤60 s each).
- **Migration**: ledger schema 2 adds `tag_kind` (`plugin` | NULL). The first model D save of a photo whose ledger tags are plain rows removes all of them (`remove_tags` deletes only astro-id's own rows; `skipped_foreign` rows are reported as `not_removed` and kept in the ledger as `legacy_tags`, schema 3, counted in the save line as "old tag(s) are yours to remove") and re-adds the summary tag as a plugin tag. The next save changes nothing. No DB edits.
- **Panel**: `names_as` in `api/state` / `api/save` switches wording (`body.names-search`): "Make names searchable at confidence ≥", "Searchable" list, save line "N searchable name(s) … old per-name tag(s) removed". Sky-label controls read `get_annotations {summary: true}` (no geometry); Export still reads the full payload. Help: Tag / Searchable name / Sky labels.
- **Manifest**: `network` = epqs.nationalmap.gov, nova.astrometry.net, cdsarc.cds.unistra.fr, vizier.cfa.harvard.edu, raw.githubusercontent.com, www.pas.rochester.edu (catalog downloads on first use). No `features` (astro-id adapts at run time). `install --older-facet` omits `network`. Id unchanged (`astro-id`). Setup (JPG/RAW, offset, ±, timezone, save cut, sky-label view, export options) is saved automatically when changed and when solving; the location when confirmed.

## Review fixes (2026-11-12, 2ff5948, 94f60e4, fda6c53)
- Per-photo ledger record in a `finally`; model D adopts `plugin_tags` sourced astro-id; ledger `legacy_tags` (schema 3).
- `put_annotations_batch` returns per-chunk results. Export: one at a time; `<stem>_<ext>_astro.jpg` when a RAW and JPG share a stem.
- Panel: non-overlapping pollers, transient error vs restart, stale locks the action buttons and tells server-newer from page-newer, "N searchable" counts indexed items (sky-label items per name), Help sentence on the coverage limit.

- Wording (2026-11-14, branch object-tags-wording): astro-id's per-object output is "object tags" everywhere (panel, Help "Words used here" defines tags / album tags / object tags / people tags, serve.py messages, CLI help, USER_GUIDE); wording only, no code identifiers, routes or JSON fields renamed.

## Panel undo (2026-11-15, branch panel-undo)
- `POST api/undo {paths?, dry_run?}` (serve.py) over `sync.undo` (sync.py): removes, for the loaded set or the given paths, what the ledger says astro-id wrote: its status tags (plate-solved / plate-solve failed / astro-review), its object tags and its sky labels. Uses the add-on's own Facet connection, so it works from the panel (the CLI `astro-id facet undo` needs FACET_URL and credentials).
- Model D Facet: status tags are plugin summary tags (`remove_tags`), object tags are the indexed names inside the sky labels (counted from `get_annotations summary`), removed with the sky labels (`clear_annotations` also clears the detections). Older Facet: object tags are plain per-object tag rows from the ledger, sky labels via `clear_annotations` / `DELETE /api/photo/annotations`.
- Never removed: tags the ledger does not list (the user's, the AI's, another tool's), `legacy_tags` (rows written through the user path that the user owns), rows a model D Facet says another writer owns (`skipped_foreign`). Counted in `not_removed` {yours, other_writer, failed, no_route} with a `why` text.
- Dry run reads only. Idempotent: a photo whose astro-id rows are all gone is dropped from the ledger, so a second undo finds nothing. Partial failure: each photo is re-read after the writes and its ledger row lists exactly what of astro-id's is still there (Undo again finishes it).
- Refused while a solve, an export, a save or another undo runs (one Facet writer at a time: `App.writing`; save and Apply tag also take that slot; a restart waits for it: "Astro will update after this save / undo."). The solve results stay, so Save to Facet can write them again.
- Panel: a quiet link under the save line in step 4, "Undo what Astro saved…", disabled with "Nothing Astro saved is recorded for the loaded photos." (or the read-only reason). Click: scope "the ticked photos" (default) or "everything loaded", dry run, one line e.g. "Remove 32 object tags, 3 status tags and the sky labels from 3 photos? Your own tags stay." with Confirm / Cancel (no browser dialog). After: the save line says what was removed, Facet refreshes those photos, the sky-label controls re-read the open photo. Help "Is it safe to repeat?" has an Undo paragraph.
