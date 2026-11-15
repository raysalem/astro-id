# Testing

1. Install uv, then `uv sync --frozen` (Python 3.12).
2. `uv run pytest -q -rs` runs the Python suite (~300 tests); `-rs` lists what skipped and why.
3. `node --test tests/js/bridge.test.js` and `node --test tests/js/panel.test.js` run the JS tests (Node 22 LTS).
4. Tests that need a Facet checkout (`facet_contract`) skip when none is found; the suite does not need ASTAP.
5. CI is `.github/workflows/ci.yml`, on push to main, pull requests and manual dispatch.
6. Job `tests` runs steps 1-3 on ubuntu-latest and windows-latest.
7. Job `examples` (Linux) installs the ASTAP CLI and W08 database (checksum-pinned, cached), solves `examples/`,
   and runs `tests/check_examples.py` against `examples/expected.json` (solved, and every listed tag at confidence >= 80).
8. `examples` skips cleanly when `examples/expected.json` is absent.
9. Run the check locally: `uv run astro-id solve examples/ --json > solve.json; uv run python tests/check_examples.py solve.json`.
10. Trigger CI by hand: `gh workflow run ci.yml --ref <branch>`.
