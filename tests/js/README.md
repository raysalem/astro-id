# tests/js: panel and bridge tests

## Brief
Node tests for the panel page's logic (`panel.test.js`) and its postMessage bridge to Facet (`bridge.test.js`). They run without a browser.

Public surface:

| Name | Kind | Use |
|---|---|---|
| `bridge.test.js` | test | bridge messages, nonce handling, update plan |
| `panel.test.js` | test | panel wording, buttons, undo, save lines |

Depends on: Node 22+, `astro_id/facet/static/`. Used by: CI.

## Tests
- Run: `node --test tests/js/bridge.test.js` and `node --test tests/js/panel.test.js`.
- Gaps: no real browser run in CI.
