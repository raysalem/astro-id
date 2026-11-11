# Facet integration

astro-id talks to [Facet](https://github.com/ncoevoet/facet) as a **sidecar**: a separate process that uses only
Facet's HTTP API with its own credential. Nothing is installed into Facet, and Facet's database is never opened
(one temporary exception for annotations, below). See the main README, section "Facet sidecar".

| Command | What |
|---|---|
| `astro-id facet sync` | solve the library's astro photos and write tags + annotations back (batch / polling) |
| `astro-id facet serve` | local web UI for an astro session (pick photos, location, time, solve, culling) |
| `astro-id facet status` | what the ledger holds for a Facet library |
| `astro-id facet undo` | remove the tags (and annotations) astro-id wrote, per the ledger |

## This folder

- `astro_notify.py.example`: an OPTIONAL in-process notifier (under 100 lines) that appends scored astro photo
  paths to a spool file for `astro-id facet sync --spool`. It never touches the database or starts a process.
  The sidecar works without it (it polls).

The earlier in-process plugin (`facet_plugin/astro_id.py`: direct SQLite writes with `created_by='astro-id'`, a
scan exit hook that spawned a detached catch-up, a hardlink into Facet's `plugins/`) was removed on 2026-11-11 in
favour of the sidecar; it is in this repository's git history.

## Facet routes used

| Route | For |
|---|---|
| `POST /api/auth/edition/login` (or `/api/auth/login` multi-user) | a session token from `FACET_EDITION_PASSWORD` |
| `GET /api/config` | Facet version (recorded in the ledger) |
| `GET /api/photos` | candidates: `category`, `path_prefix`, `tag`, `album_id`, `date_from`; every hide toggle off |
| `GET /api/photo` | one photo: GPS, capture date, `date_manually_set`, manual and AI tags, `raw_companion` if present |
| `POST /api/photos/batch_manual_tags`, `DELETE /api/photo/manual_tags` | tags (stored as user tags until Facet has plugin-owned tags) |
| `PUT /api/photo/gps` | a location set from the session UI |
| `PUT` / `GET` / `DELETE /api/photo/annotations` | annotations, when the server has them (feature-detected) |

## Annotations without the PUT route (temporary)

Until Facet ships `PUT /api/photo/annotations`, `--legacy-db PATH --facet-dir <facet checkout>` writes the payload
with Facet's own `db/annotations.py` (validation + upsert) straight into that database. It is labelled TEMPORARY
in the code and the output, is only for a local stack with the `photo_annotations` table, and is ignored as soon as
the server has the PUT route. Without either, annotations are skipped with a warning and tags are still written.
