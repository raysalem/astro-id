# tests: the Python suites

## Brief
The pytest suites for the engine and the Facet add-on, plus fakes of Facet (HTTP and MCP) and the example-set check. JavaScript tests are in `js/`; scripts that need a real Facet are in `live/`.

Public surface:

| Name | Kind | Use |
|---|---|---|
| `conftest.py` | fixtures | shared fixtures |
| `fake_facet.py` | fake | a fake Facet HTTP API |
| `fake_facet_mcp.py` | fake | a fake Facet MCP server (old and model D contracts) |
| `check_examples.py` | command | compares a solve of `examples/` with `examples/expected.json` |
| `js/` | folder | Node tests ([README](js/README.md)) |
| `live/` | folder | scripts against a real Facet ([README](live/README.md)) |

Depends on: `astro_id/`, pytest. Used by: CI.

## Tests
- Run all: `uv run pytest -q -rs`.
- Tests that need a Facet checkout (`facet_contract`) skip when `FACET_DIR` is unset.
- Gaps: see [TESTING](../docs/TESTING.md).
