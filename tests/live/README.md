# tests/live: scripts against a real Facet

## Brief
Manual scripts that run the add-on against a real Facet on a COPY of a library. They are not part of CI and never touch a live library.

Public surface:

| Name | Kind | Use |
|---|---|---|
| `p3_model_d.py` | script | the model D proofs on a copy (migration, searchable object tags, second save writes nothing) |
| `p3_addon_http.py` | script | the add-on's HTTP API against a running Facet |

Depends on: a Facet checkout with add-ons on, `astro_id/`. Used by: the maintainer, by hand. Setup steps are in each script's header.

## Tests
- These are the tests; run them by hand as the header says.
- Gaps: not automated.
