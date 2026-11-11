"""astro-id as a Facet sidecar: talks to Facet only over its HTTP API (``astro-id facet sync|serve``).

- ``client``: the HTTP client (login, photo listing, tags, GPS, annotations feature detection).
- ``ledger``: local SQLite record of what astro-id wrote into which Facet library.
- ``sync``: tag planning and the solve -> write pipeline shared by ``sync`` and ``serve``.
- ``legacy``: TEMPORARY direct annotation writes (``--legacy-db``) until Facet has ``PUT /api/photo/annotations``.
- ``serve``: the local web UI for an astro session.
"""
