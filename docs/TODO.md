# TODO

Backlog, one line each. Status as of 2026-11-15 (main = 8c8ee5c, the Moon release). Closed items and older per-work-stream detail: [archive/TODO-history-2026-11.md](archive/TODO-history-2026-11.md).

## Needs the owner's check or call
- [ ] Open the Astro panel in Facet; expect "Updating Astro…" then the panel at page 2026-11-15.5 (Undo link, Sky labels switch, requirements line). Then the human checks in [TESTING](TESTING.md). · opened 2026-11-15
- [ ] Model D by eye on a Facet running the model D contract (the headless proof passed; see [ISSUES](ISSUES.md) P3). · opened 2026-11-15
- [ ] Location pick (Google Earth, confirm, last site) has no check on the current panel. · opened 2026-11-15
- [ ] Prune old worktrees and branches that are merged (integration, panel-undo, horizon-time-fix, safe-update, save-cut, p3-real-contract, local-main-work, release) once testing is done. · opened 2026-11-15
- [ ] Decide what to do with the `local-main-work` branch on the remote (its history holds a private address; no history rewrite without the owner's word). · opened 2026-11-15

## Engine
- [ ] Horizon-based time pin (foreground/sky boundary, fit altitude 0). Only the Moon pins `best_fit_time` today.
- [ ] Solve across photos to pin time and location together, with an error bar, offered as "Use" like Auto time. · opened 2026-11-15
- [ ] Twilight/foreground frames do not solve: a bright-object solve could match planets and 1st-magnitude stars at a known time.
- [ ] Badly defocused frames solve with RMS > 2 px: a donut-centroid detector would help.
- [ ] Quality metrics grade some twilight frames C although they plate-solve at grade A; a cull rule would reject them.
- [ ] Label placement: names overlap (URSA MINOR / Little Dipper, CASSIOPEIA / W of Cassiopeia); near an edge the text should go inward; one collision pass shared by the burned copy and Facet's layer.
- [ ] RAW without a JPG (rawpy, half size) is untested on a real RAW-only folder; RAW solve is not clearly better than the JPG.
- [ ] astrometry.net fallback is implemented but untested (no key); Linux/macOS ASTAP discovery is minimal.
- [ ] Moon: a real camera photo at 250+ px with EXIF time and site; eclipse and thin-crescent handling; run `moon` from `label`/`solve` when a solve fails on a large disc. The first version is done (`astro-id moon`); the full list is under "What is left" in [the Moon plan](plans/2026-11-15-moon-features.md). · opened 2026-11-15

## Facet add-on
- [ ] Panel results are lost on a full page reload; restore the last solve from the server's job state (the held photo set already restores).
- [ ] CLI `astro-id facet undo` still uses the older `remove_all`; call `sync.undo`.
- [ ] Delete the TEMPORARY env-file credential path in `addon_credentials()` and the `--legacy-db` writer once Facet ships add-on tokens and the annotations route.
- [ ] Plugin tags through `POST /api/photos/plugin_tags` once its body is pinned.
- [ ] Self-update does not sync dependency changes (`pyproject`); `index.html`/`bridge.js` edits are not in the update fingerprint.
- [ ] Firefox check of the sandboxed page; cancel button and SSE instead of polling.
- [ ] Facet side (asks, not astro-id code): album id for a photo opened from an album; browsed folder in the context; `server_version` empty from a checkout; a supervisor `restart` or bridge `restart_addon`; Windows Stop leaving uv children.
- [ ] Phone use is not recommended (the panel needs width): say "use a desktop-width window" in Help next time the text is touched.
- [ ] Optional: a simple drag-and-drop app on top of `astro-id label`.

## Docs
- [ ] Third-party notices: confirm the README lists the licenses of downloaded data (OpenNGC CC-BY-SA, Stellarium constellation lines GPL-2, Hipparcos, DE421).
- [ ] DETAILS: the dated sections stop at 2026-11-11 for the add-on design; fold in model D, the keeper and safe update (see FEATURES).
- [ ] The `tools/check_docs.py` link check and `dev-notes/tools/check.py` overlap; keep one in CI when the owner decides.
