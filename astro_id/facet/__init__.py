"""astro-id as a Facet sidecar: talks to Facet only over its HTTP API (``astro-id facet sync|serve``).

- ``client``: the HTTP client (login, photo listing, tags, GPS, annotations feature detection).
- ``ledger``: local SQLite record of what astro-id wrote into which Facet library.
- ``sync``: tag planning and the solve -> write pipeline shared by ``sync`` and ``serve``.
- ``legacy``: TEMPORARY direct annotation writes (``--legacy-db``) until Facet has ``PUT /api/photo/annotations``.
- ``serve``: the astro session web UI, standalone (``facet serve``) or as a Facet add-on (``facet serve --addon``,
  started by Facet; PLUGIN_BOUNDARY.md v0.2). ``static/bridge.js`` is the sidebar's postMessage v1 bridge.
- ``install``: ``facet install | uninstall``, the add-on manifest ``<facet data>/addons/astro-id/addon.json``.
- ``lifeline``: the add-on exits when the process Facet started (uv) or Facet itself ends.
"""
