# Testing

1. Install uv, then `uv sync --frozen` (Python 3.12).
2. `uv run pytest -q -rs` runs the Python suite (561 tests on 2026-11-16); `-rs` lists what skipped and why.
3. `node --test tests/js/bridge.test.js` and `node --test tests/js/panel.test.js` run the JS tests (Node 22 LTS).
4. Tests that need a Facet checkout (`facet_contract`) skip when none is found; the suite does not need ASTAP.
5. CI is `.github/workflows/ci.yml`, on push to main, pull requests and manual dispatch.
6. Job `tests` runs steps 1-3 on ubuntu-latest and windows-latest; on Linux it first runs `uv run python tools/check_docs.py`
   (every relative link, image and #anchor in the docs resolves; no personal paths).
7. Job `examples` (Linux) installs the ASTAP CLI and W08 database (checksum-pinned, cached), solves `examples/`,
   and runs `tests/check_examples.py` against `examples/expected.json` (solved, and every listed tag at confidence >= 80).
8. `examples` skips cleanly when `examples/expected.json` is absent.
9. Run the check locally as CI does: `uv run astro-id solve examples/ --json --no-fallback --overlay-dir <scratch> > solve.json`,
   then `uv run python tests/check_examples.py solve.json examples/expected.json`.
10. Trigger CI by hand: `gh workflow run ci.yml --ref <branch>`.
11. Rebuild the example set (the source photos are not in the repo): `uv run python tools/make_examples.py prep
    <source-folder>` (2400 px copies, EXIF stripped to what astro-id reads), solve as in step 9 with
    `--overlay-dir examples/annotated`, then `make_examples.py expected solve.json` (writes `expected.json` with each
    listed tag's `scores`) and `make_examples.py composites` (hero and gallery images). Then
    `uv run --with imageio-ffmpeg python tools/make_demo.py` rebuilds `docs/img/demo.mp4` and `demo.gif`.
12. The demo (`tools/make_demo.py`, one command above; needs ASTAP + W08, about a minute) solves frames 04 and 05 four
    times into `build/demo/` (site + offset, offset only, site only, neither), renders each scene with
    `astro_id.export.render` for the view the card beside it shows, and writes the numbers it used to
    `build/demo/numbers.json`. It stops if a run no longer matches a caption. `--reuse` skips the solves.
13. Label placement (`tests/test_labels.py`) runs on the real label inputs of the 5 sky and 4 Moon examples in
    `tests/fixtures/labels/`; after a change to detection or the examples, rebuild them with
    `uv run python tools/make_label_fixtures.py` (needs ASTAP + W08, like step 9).
