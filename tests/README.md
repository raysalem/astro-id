# tests: the Python suites

## Brief
The pytest suites for the engine, the Moon command and the Facet add-on, with fakes of Facet (`fake_facet.py` for HTTP, `fake_facet_mcp.py` for MCP, old and model D contracts) and `check_examples.py`, which compares a solve of `examples/` with `examples/expected.json`. Node tests are in [js/](js/README.md); scripts that need a real Facet are in [live/](live/README.md).

- Depends on: `astro_id/`, pytest. Used by: CI.
- Gotchas: the suite does not need ASTAP. Tests that need a Facet checkout (`facet_contract`) skip when `FACET_DIR` is unset; some Moon ephemeris checks skip without a cached DE421.

## Tests
- Run all: `uv run pytest -q -rs` (`-rs` lists what skipped and why).
- Gaps: see [TESTING](../docs/TESTING.md).
