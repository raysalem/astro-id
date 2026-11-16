# tools: docs check and builders

## Brief
Scripts for the repo itself, not part of the installed package.

- `check_docs.py`: every relative link, image and anchor in the docs resolves, and no personal text slipped in. Runs in CI.
- `make_examples.py` and `make_demo.py`: rebuild the example set, its `expected.json`, the composites and the demo video/GIF. They need ASTAP and the source photos (not in the repo).
- `make_label_fixtures.py`: captures the label inputs of the 5 sky and 4 Moon example runs into `tests/fixtures/labels/` for `tests/test_labels.py` (needs ASTAP + W08 and DE421).
- `make_moon_catalog.py`: regenerates `astro_id/moon_catalog.py` from the public-domain IAU/USGS gazetteer; download steps are in its header.
- Depends on: Pillow, ASTAP (builders only). Used by: CI (`check_docs.py`), the maintainer.

## Tests
- `uv run python tools/check_docs.py` (CI job `tests`, Linux).
- Gaps: the builders are run by hand ([TESTING](../docs/TESTING.md)); `make_moon_catalog.py` has no test of its own, `tests/test_moon.py` checks the catalog it produced.
