# The simplest way to use astro-id

1. Install ASTAP and [uv](https://docs.astral.sh/uv/) once; `uv run astro-id setup` downloads the star data and `uv run astro-id check` shows anything still missing ([requirements](REQUIREMENTS.md)).
2. **Windows:** drag a photo (or a folder) onto `astro-label.cmd`. **Linux/macOS:** `./astro-label.sh photo.jpg`.
3. Or from a terminal in the repo: `uv run astro-id label <photo-or-folder>`.
4. You get `<name>_astro.jpg` next to the photo: a copy with constellation figures and names burned in. The original is never touched.
5. Want data instead? `--out sidecar` writes `<name>.astro.json` (objects, confidence, positions, annotations); `--out both` writes both; `--to <folder>` puts results elsewhere.

Time and place only matter for planets and the Moon. Without them stars, constellations and deep-sky objects are still labeled. Add `--offset` / `--site`, or an `astro-id.json` in the photo folder, if you know them.

Re-running skips photos that already have their output. Exit code: 0 labeled (or already done), 1 nothing labeled, 2 setup problem.
