# tools: docs check and builders

## Brief
Helper scripts for the repo itself: the docs link check, and the builders for the example photos and the demo video.

Public surface:

| Name | Kind | Use |
|---|---|---|
| `check_docs.py` | command | every relative link, image and anchor in the docs resolves; no personal paths |
| `make_examples.py` | command | prepares the example set, its `expected.json` and composites |
| `make_demo.py` | command | rebuilds `docs/img/demo.mp4` and `demo.gif` |

Depends on: Pillow, ASTAP (for the builders). Used by: CI (`check_docs.py`), the maintainer.

## Tests
- `check_docs.py` runs in CI: `uv run python tools/check_docs.py`.
- Gaps: the builders are run by hand ([TESTING](../docs/TESTING.md)).
