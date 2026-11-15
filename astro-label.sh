#!/bin/sh
# astro-label.sh - label night-sky photos with astro-id (Linux/macOS).
#   ./astro-label.sh photo.jpg            writes photo_astro.jpg (a labeled COPY) next to it
#   ./astro-label.sh /path/to/folder --out both --to /path/to/labeled
# With no argument it asks for a path. Originals are never modified.
cd "$(dirname "$0")" || exit 2
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it from https://docs.astral.sh/uv/ then run this again." >&2
  exit 2
fi
if [ $# -eq 0 ]; then
  printf 'Photo or folder to label: '
  read -r target
  set -- "$target"
fi
exec uv run --quiet astro-id label "$@"
