# astro_id/facet: the Facet add-on

## Brief
Everything that talks to the Facet photo library. Facet starts the add-on process; it serves the sandboxed Astro panel, reads photos over Facet's MCP and HTTP API with its own token, and saves object tags and sky labels only when the user clicks Save. A local ledger records what astro-id wrote, so Undo removes exactly that.

- Commands: `astro-id facet install | uninstall | serve | sync | status | undo | adopt` ([REFERENCE](../../docs/REFERENCE.md)).
- The keeper (`keeper.py`) is what Facet starts. It runs the panel server as a worker and restarts it when the code in its checkout changes, after a safety probe of the new code; if the new worker fails, the keeper answers by itself. So never edit a checkout a running add-on uses.
- Panel (`static/index.html` + `static/bridge.js`, inlined at serve time): setup, solve, results and Save; Undo what Astro saved; the "Sky labels on this photo" switch; a requirements line (the `astro-id check` rules in `requirements.py`, shown only when something is missing); a warning when a photo's sky was below the horizon at the camera time. `PAGE_VERSION` in `index.html` and `serve.py` must match (`tests/test_facet_addon.py` asserts it).
- `legacy.py` is a TEMPORARY direct database writer for a Facet without the annotations route; remove it when Facet ships that route ([TODO](../../docs/TODO.md)).
- Depends on: `astro_id/` (the engine), the `mcp` package. Used by: Facet, the `astro-id facet` commands. Design and history: [DETAILS](../../docs/DETAILS.md), [FEATURES](../../docs/FEATURES.md).

## Tests
- Covered by: `tests/test_facet_*.py`, `tests/test_self_update.py`, `tests/test_safe_update.py`, `tests/js/`.
- Run just these: `uv run pytest -q tests -k "facet or update"`, then `node --test tests/js/bridge.test.js` and `node --test tests/js/panel.test.js`.
- Gaps: in CI the panel runs only against a stub; checks inside a real Facet are listed in [TESTING](../../docs/TESTING.md) and `tests/live/`.
