# astro-id: usage

## The simplest way
Drag a photo or folder onto `astro-label.cmd` (Windows), run `./astro-label.sh photo.jpg` (Linux/macOS), or `uv run astro-id label <photo-or-folder>`. Each photo gets a labeled copy `<name>_astro.jpg` next to it. Details: [SIMPLE](SIMPLE.md). Every option: [REFERENCE](REFERENCE.md). Full manual: [MANUAL](MANUAL.md).

## A close-up of the Moon
`uv run astro-id moon <photo> --time 2026-11-20T06:00Z --site 32.6,-116.3` writes `<name>_moon.jpg` with the maria, large craters, ranges and Apollo sites named. The disc must be about 250 px across for craters. Details: [MANUAL](MANUAL.md#photos-of-the-moon-astro-id-moon).

## Inside Facet
Install the add-on once (`uv run astro-id facet install --facet-data <Facet data folder>`), then open **Astro** from Facet's add-on tab. The sections below describe the engine and the panel.

## Errors and exit codes
See [MANUAL](MANUAL.md) (troubleshooting) and the panel's Help page.

## Troubleshooting
- "Astro was updated: stop and reopen it": an add-on process that predates the keeper. Stop it on Facet's Add-ons page, then Open it once; later updates are automatic.
- Nothing solves: run `uv run astro-id check` and see [REQUIREMENTS](REQUIREMENTS.md).

---

*Moved from the maintainer's private working notes on 2026-11-15 and scrubbed for this public repo. Sections are dated; later sections replace earlier ones where they say so.*

## Engine
1. One-time: install the ASTAP CLI + W08 (README "Install"), `uv sync`, `uv run astro-id setup`.
2. Per photo folder: `uv run astro-id site "<folder>"`. Chrome opens Google Maps: right-click the spot, click the first line (copies lat, lon), paste it. Confirm the elevation and a name. Enter the camera clock offset if the camera clock was wrong (e.g. `+2559d11h08m`), blank if it was right. This writes `astro-id.json` in the folder.
3. `uv run astro-id solve "<folder>"`. Overlays and `astro-id-report.csv` land in `<folder>-annotated` (or `--overlay-dir`). In the overlays green = high, amber = likely, gray = candidate; the number is the confidence.
4. Without `astro-id.json`, pass `--offset` and `--site lat,lon,elev`, or accept EXIF time (planets and Moon then stay "candidate").

## Facet add-on (2026-11-11) - astro-id inside Facet
Once per machine (PowerShell):
1. `cd <local path>`
2. `uv run astro-id facet install --facet-data "$env:LOCALAPPDATA\Facet\data"` (for the test copy: `...\Facet\data\astro-e2e`). It prints the manifest it wrote.
3. Facet's operator switch `"addons": {"enabled": true}` must be on in that data folder's `scoring_config.json`. Restart Facet (or reload its config). "Astro" appears on Facet's Add-ons page.

Then, in Facet only:
- Import: in the import dialog pick "After import, open: Astro". When the scan is done, click "Open Astro for N photos". The sidebar shows those photos (category astro; clear the box and press Reload for every photo of the run).
- Any time: open Astro from the Add-ons page. It uses the current view, or the photos you selected.
- Walk the steps: 2 Location (paste `lat, lon` or a Maps link for photos without one), 3 Time (offset, ± tolerance, RAW/JPG; Preview), 4 Plate solve, 5 Culling (keep/review/reject; "Show review" and the others select those photos in Facet). Tags and locations show up in Facet right after. Annotations appear only once Facet has the annotations route (the sidebar says when they are skipped).

Remove: `uv run astro-id facet uninstall --facet-data "$env:LOCALAPPDATA\Facet\data"`. To also remove the tags astro-id wrote: `uv run astro-id facet undo`.

## Setting the location (2026-11-11) - replaces "paste lat, lon for photos without one" above
In the Astro panel, step 2 Location:
1. **For**: pick the folder (or an album). A location is always set for the whole folder or album.
2. Click **Google Earth** (or **Google Maps**): it opens in a new tab. Find the spot. Copy the page address (or the coordinates).
3. Paste it into the box and click **Check**. You see latitude, longitude, elevation, the folder or album, and how many photos will change. Nothing is written yet.
4. Type a site name and click **Confirm**. Photos without GPS get the location; photos with camera GPS keep theirs unless you tick "also replace camera GPS". The site is also saved in the folder's `astro-id.json` and offered first next time (**Last site**).
You can also type `lat, lon` or `lat, lon, elevation` (decimal) or degrees-minutes-seconds like `32°36'23"N 116°19'26"W 1100 m`.

## The Astro panel, priority-first (2026-11-12, later) - replaces "Setup, then Run" above
There is no **Save setup** button any more: the setup saves itself for the folder or album when you change it and when you solve.

**Top of the panel: load the photos** (one click)
- Open a photo or an album in Facet. The top button names it: **Load folder astro (18 photos)** (the open photo's folder) or **Load album Astro test (18 photos)**. Photos ticked in the Gallery or an import that opened the panel are offered the same way. "setup saved" after the name means you used it before.
- Or pick an **Album** from the list (it loads at once), or **Import a new folder...** and paste its path (Facet must already have the photos).
- A folder or album used before opens with its setup applied and the next useful step open (usually 3. Plate solve). A new one opens at 1. Location.
- **Photos (18)** under the name: the per-photo list, JPG or RAW, Show in Facet.

**Steps, in order** (only the step that matters now is open)
1. **Location**: Google Earth / Maps, paste, **Check**, **Confirm**.
2. **Time**: offset and ± minutes (timezone and a check of the corrected times are folded under them). **Auto**: after a solve, if the Moon was found in a solved frame, the panel says e.g. "The Moon in 2 solved frames (...) suggests the clock is +2559d11h06m ±3 min" with a **Use ...** button. Use it, then solve again. Without the Moon in a solved frame there is no suggestion (planets and the horizon do not pin the time yet).
3. **Plate solve**: one button. Shows "Solving 3 / 18 · IMG_0012.JPG solved" and a bar. The log is folded.
4. **Results and object tags**: as before (ticks, "Save object tags at confidence ≥", **Save to Facet**).
5. **Sky labels on the photo**: open a solved photo in Facet; Show, groups, "Show names at confidence ≥", Max magnitude, show scores.
6. **Export labeled copies**: burns exactly what step 5 shows into JPG copies in "<folder>-annotated". Choose at export time: **Draw constellation stick figures**, **Show confidence numbers after the names**, **Add an info box (date, time, solve grade)**. Exports the ticked solved photos of this session, or, after a reload, every loaded photo that has sky labels in Facet (the others are listed under "Not exported"). No new solve.
- **Culling (low priority)**: last, folded, unchanged.

### Object tags on a newer Facet (model D, 2026-11-12; wording 2026-11-14)
With a Facet that has the new add-on contract (fix/review-all), Astro stops writing one ordinary tag per object and stores object tags instead:
- **Tags**: Astro writes only `plate-solved` or `plate-solve failed` (plus `astro-review` from Culling). Your own tags are untouched and Astro never fills your 50-tag list.
- **Object tags**: step 4 is **Save object tags at confidence ≥** (default 65). The objects at or above it are saved with the sky labels, and Facet's search finds them (type `orion` or `jupiter` in the gallery search). Only objects that also have a sky label on the photo get an object tag (very faint stars, objects off the frame and names Facet refuses are left out); the count next to the slider shows exactly how many. Raise or lower it and **Save to Facet** again to drop or add object tags.
- **Sky labels**: unchanged; every object found is still drawn, and step 5's slider still only changes what is shown.
- First save on photos an older Astro tagged: the old plain per-object tags (`orion`, `m42`...) are removed and object tags replace them. The save line says how many old tags went. Old plain per-object tags that were written outside the add-on (for example by the command-line sync with a password) are yours: Astro leaves them and the save line says "N old tag(s) are yours to remove".
- Sky labels off, or a token that cannot store them: Astro keeps writing plain per-object tags (object tags cannot be stored, and it never removes your old tags then).
- If the panel says Astro was updated or restarted, its buttons are disabled: reopen the panel (or restart the add-on from Facet's Add-ons page when told to). One missed answer from the add-on is no longer treated as a restart.
- An older Facet: the panel shows the same "Save object tags at confidence ≥" and stores them as ordinary tags, as before.
- Install on an older Facet: `uv run astro-id facet install --older-facet --facet-data ...` (an older Facet refuses the new `network` line in the manifest).

### When Astro is updated (2026-11-14)
Nothing to do. When Astro's code has changed, the panel shows "Updating Astro…" for a second or two, then carries on where you were: the loaded photos and the last solve (saved or not) are still there.
- If a plate solve or an export is running, the panel says "Astro will update after this solve." (or export) and updates when it ends.
- If the panel itself is old, it reloads after the update; use the Load button on the first line to load your photos again. If you have unsaved results, it first says "Astro will update after you save these results to Facet."
- Only if the panel says "Astro needs one restart to update": on Facet's Add-ons page click **Stop**, then **Open**. That is needed once, for the Astro version that was running before this feature.
- (2026-11-15) An update is tried out first. While new files are still being written you see nothing; Astro waits until they have been quiet for about 10 seconds, then checks that the new version starts before switching.
- "An update to Astro is not ready yet; still running the previous version." The new version did not start. Keep working: everything still works on the previous version. Nothing to click; Astro tries again by itself as soon as the files change.
- "An update to Astro did not start; Astro will try again as soon as its files change." Rare: the new version started in its trial but stopped right after the switch. Astro cannot work until a fixed version arrives, but your loaded photos and unsaved results are kept and come back by themselves. No clicks needed.
- "Astro did not come back after updating: on Facet's Add-ons page click Stop, then Open." Only with an Astro started before this safety net (once, after it is installed).

### Is an album loaded? (2026-11-14)
The first line of the panel tells you. "Nothing loaded yet." means click the Load button (it names what Facet shows). "Loaded: Album X . 18 photos" with a tick means you are working on what Facet shows. If Facet shows another album you also see "Facet is showing: Album Y" and a "Load Album Y instead" button; your loaded set stays until you click it. The setup lines below are about the loaded set only.

## Undo what Astro saved
In **4. Results**, under the save line, click **Undo what Astro saved…**. Pick **the ticked photos** or **everything loaded**. The panel says what it will remove, e.g. "Remove 32 object tags, 3 status tags and the sky labels from 3 photos? Your own tags stay." Click **Confirm** (or **Cancel**). Only Astro's object tags, status tags and sky labels go; your own tags stay. The results stay in the panel, so **Save to Facet** puts them back. Doing it twice is harmless.
