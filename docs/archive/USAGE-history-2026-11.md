# Usage history (superseded sections)

*Moved from the maintainer's private working notes on 2026-11-15 and scrubbed for this public repo. Sections are dated; later sections replace earlier ones where they say so.*


## Facet plugin
See `facet_plugin/README.md`: install into `<facet>/plugins/`, add the `plugins.astro_id` block to the scoring config override, restart. Backfill with `python plugins/astro_id.py <folder> --dry-run`, then without `--dry-run`. Changing `--min-confidence` and re-running adds/removes the 65-79 band tags.

### Facet as source of truth (2026-11-09)
- Set the location in Facet (Set location) and fix the camera clock with the capture-date shift; the plugin uses both automatically. astro-id.json is then only a fallback (and holds the folder timezone). If a photo has neither Facet GPS nor a json site, time-dependent IDs (planets, Moon) are capped.

### Tag hierarchy + plugin-controlled runs (2026-11-10)
- Default tags: constellations, Sun/Moon/planets, bright Messier/NGC/IC (mag <= 6) and asterisms such as `orion's belt`. Individual stars are not tagged; to tag the brightest ones add `"star_mag_limit": 1.5` to `plugins.astro_id` (or `--star-mag-limit 1.5` on the engine). For tags like `planet/jupiter` set `"tag_style": "kind"`. Re-run the backfill after a change.
- Overlays and `astro-id-report.csv` still show every star; the CSV `taggable` column says what became a tag.
- Bursts: frames shot back to back with the same settings are solved once; the overlay legend of a reused frame says `stack-copy of <file>`. To solve every frame set `"stack_mode": "all"`.
- Scanning: just run the Facet scan. When it finishes, a hidden catch-up starts for astro photos without astro-id tags and writes them in the background (a minute or two per folder). Progress: `<local app data>`. To run it by hand: `python plugins\astro_id.py --pending` from the Facet checkout with Facet's python.

## Overlay labels, RAW solving, XMP keywords (2026-11-10)
- The overlay JPG now shows names only. Add `--overlay-scores` (or plugin `"overlay_scores": true`) for "Uranus 100". The live overlay with confidence, group and magnitude sliders is in the Facet viewer once Facet has the annotations table; the plugin fills it on every solve.
- Solve the RAW instead of the JPG: `--solve-source raw` (or `auto`), `astro-id.json` `"solve_source": "raw"`, or plugin `"solve_source": "raw"`. Default is the JPG. Everything stays in the JPG pixel space.
- Keywords in the RAW sidecar: set plugin `"tag_targets": ["facet", "raw_xmp"]`, run the backfill with `--dry-run` first and read the `xmp ...` lines, then run without it. Only keywords the plugin wrote are ever replaced.
- Every option, flag, `astro-id.json` key and plugin key is in README.md "All options".

## Astro session and culling (2026-11-11)
- Facet no longer solves during a scan by default (`trigger: "manual"`). To get the old behaviour add `"trigger": "scan"` to `plugins.astro_id`.
- Confirm the time first: `uv run astro-id session --paths-file photos.json --preview --offset +2559d11h08m --tolerance-min 5 --tz America/Los_Angeles`. Each line shows the camera time, the corrected time and its window, and where the site and timezone come from. `photos.json` is a JSON list of photo paths.
- Solve: the same command without `--preview` (add `--save-folder-config` to remember offset/tolerance/timezone in each folder's `astro-id.json`, `--source raw` to solve the RAW twins). One line per photo as it finishes; the last line has the cull verdicts.
- Culling: `review` = look at it (defocused frames land here and are kept for stacking), `reject` = trailed beyond 3:1 or under 20 stars. Change the rule with `--cull-rule '{"order": ["focus", "stars"], "thresholds": {"focus_ratio": 2.0}}'` or a `"cull"` block in `astro-id.json`; `astro-id cull --metrics-file` re-ranks without solving. Nothing changes Facet's own cull flags.
- In Facet the metrics sit in each solved photo's annotation payload (`quality`), ready for the session page.


## Facet sidecar (2026-11-11) - replaces every "Facet plugin" step above
The plugin is gone; nothing of astro-id is installed in Facet any more. astro-id talks to Facet over its web API.
1. Once per PowerShell window: `$env:FACET_URL = "http://127.0.0.1:<facet-port>"` and `$env:FACET_EDITION_PASSWORD = "<your Facet edition password>"`.
2. Session UI: `cd <local path>` then `uv run astro-id facet serve`. The page opens in the browser (the address with the long random part is printed too). On the page: 1 load photos by folder prefix (or album/tag; clear "Category" to include photos Facet did not classify as astro), 2 check locations (paste `lat, lon` or a Google Maps link into "Set location", or click "in Facet"), 3 enter the clock offset, tolerance and timezone and press "Preview times" (optional: "Save to folder astro-id.json"), 4 press "Plate solve" and watch the log, 5 read the culling table, change the rule and press "Re-cull". Ctrl+C in the window stops it.
3. Batch: `uv run astro-id facet sync --path-prefix "<folder as Facet shows it>" --category '""' --dry-run` shows what would change; without `--dry-run` it writes. `--since last` picks up only new photos. `uv run astro-id facet status` lists what astro-id wrote; `uv run astro-id facet undo --path-prefix "<folder>"` takes it back.
4. Sky overlay on the photo page: until Facet has its write route, add `--legacy-db "$env:LOCALAPPDATA\Facet\data\photo_scores_pro.db" --facet-dir <local path>` (temporary) to `sync` or `serve`. Without it, tags are written and the overlay is skipped with a note.
5. First time on a library the old plugin already tagged: run `uv run astro-id facet adopt --legacy-db <that db>` once, so astro-id can replace or remove its old tags later.

## The Astro panel: Setup, then Run (2026-11-12) - replaces the step lists above
Four kinds of tag in Facet, one name each, never mixed (2026-11-14):
- **Tags**: describe the image (the AI's and the ones you type). Astro adds only 1-3 status tags: `plate-solved`, `plate-solve failed`, `astro-review`.
- **Album tags**: only for filtering. Astro does not use them.
- **Object tags**: what an add-on finds in the image. Astro's are the sky objects it names (`orion`, `jupiter`...); Facet's search and filters find them. An older Facet stores them as ordinary tags; same word, same meaning.
- **People tags**: the persons. Astro does not write them.

Two more words, not tags:
- **Sky labels**: the names Facet draws over the photo (constellation lines, planets, Messier objects). Stored in Facet, drawn live, controlled from the panel.
- **Labeled copies**: separate JPG files with the labels burned in, for sharing outside Facet. Off unless you tick them.

**A. Setup for this folder or album** (once per folder or album)
1. **Photos**: pick **Folder** (paste the path) or **Album** (Facet's regular albums), then JPG or RAW. Opening Astro from an import or from ticked photos fills this for you.
2. **Location**: Google Earth / Maps, paste, **Check**, **Confirm** (saved at once).
3. **Time offset**: the camera clock error, ± minutes, timezone; **Preview times** to check.
4. Click **Save setup**. It goes into every folder's `astro-id.json`, and into the album when you loaded one. Next time the folder or album opens with all of this filled in.

**B. Run**
1. **Plate solve**. Tick **make labeled copies** first if you want JPG copies. Solving only reads: nothing changes in Facet yet.
2. **Results**: one row per photo: solved?, grade, and the objects (click "N object tags · M found" for the objects and their confidence; objects below the save cut are greyed). Untick any photo you do not want saved (failed solves start unticked).
3. **Save object tags at confidence ≥** (default 65) decides what is **saved**: objects at or above it become object tags. Then click **Save to Facet**: object tags, the `plate-solved` status tag and sky labels for the ticked photos. Clicking it again changes nothing; saving again with a new value adds or removes object tags to match. The value is kept with the setup. Tags you typed yourself are never removed, even with the same text as an object. On a Facet that does not say who wrote a tag (older versions), tags with an object's name that astro-id has no record of are left alone, and the save line says how many ("... left as they were"); run `astro-id facet adopt` once on that library to hand them back to astro-id.
4. **Sky labels**: open a solved photo in Facet. The panel shows **Show sky labels** (off at first), the groups (constellations, planets & Moon, Messier/NGC/IC, asterisms, stars), **Show names at confidence ≥**, **Max magnitude** and **show scores**. This slider decides what is **shown**, never what is saved as object tags. Facet redraws as you change them. **Save setup** keeps these settings (not Show) for the folder or album.
5. **Culling (low priority)**: keep / review / reject per frame, advisory only; "Apply tag" adds `astro-review` to the review/reject frames. It is enabled after **Save to Facet** and touches only photos already saved.

**Help** (button at the top of the panel): the flow, the words (the four tag kinds, sky label, labeled copy, confidence, grade), the two sliders, setup details, culling. The "More" links in steps 5 and 6 jump to the sliders section.

