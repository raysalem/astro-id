# astro_id/facet: the Facet add-on

## Brief
Everything that talks to the Facet photo library: the add-on process Facet starts (a keeper that supervises a worker), the panel server and its static page, the MCP and HTTP clients, the sync that saves tags and sky labels, and the local ledger of what was written. See [DETAILS](../../docs/DETAILS.md) and [FEATURES](../../docs/FEATURES.md).

Public surface:

| Name | Kind | Use |
|---|---|---|
| `cli.py` | command | `astro-id facet install\|uninstall\|sync\|serve\|status\|undo\|adopt` |
| `install.py` | module | writes or removes the add-on manifest |
| `keeper.py` | module | the process Facet starts; restarts the worker on code changes after a safety probe |
| `serve.py` | module | the panel's HTTP server and API (`api/...`) |
| `static/` | folder | the sandboxed panel page (`index.html`) and its bridge script |
| `mcp_client.py` | module | Facet's MCP endpoint client |
| `client.py` | module | Facet's HTTP API client (stand-alone use) |
| `sync.py` | module | save, undo and migration of tags and sky labels |
| `ledger.py` | module | local record of what astro-id wrote |
| `lifeline.py` | module | exit when Facet or uv ends |
| `legacy.py` | module | TEMPORARY direct database writer, to be removed |

Depends on: `astro_id/` (the engine), the `mcp` package. Used by: Facet (starts it), the `astro-id facet` commands.

## Tests
- Covered by: `tests/test_facet_*.py`, `tests/test_self_update.py`, `tests/test_safe_update.py`, `tests/js/`.
- Run just these: `uv run pytest -q tests -k "facet or update"` and `node --test tests/js/`.
- Gaps: the panel page runs only against a stub in CI; real-Facet checks are in [TESTING](../../docs/TESTING.md).
