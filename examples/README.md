# Example photos

Five frames from one night at a public dark-sky star-party site (Tierra del Sol, California), 7-8 November 2026.
Sony A7R IV, FE 20 mm F1.8 G, f/1.8, 8-13 s, ISO 1000-3200, untracked. All five are 2400 x 1600 px (downscaled
from 9504 x 6336) and stripped to the EXIF astro-id needs (time, focal length, exposure, camera and lens model).

| File | Local time | Grade | What is in it (confidence) |
|---|---|---|---|
| `01-cassiopeia-perseus.jpg` | 20:34 | A | Cassiopeia's W, Cepheus, Perseus, Auriga and the Little Dipper (100), the Double Cluster NGC 869/884 (97/96), the Pleiades at the right edge (86); a power pole on the horizon |
| `02-milky-way-cepheus.jpg` | 20:37 | A | The autumn Milky Way through Cassiopeia and Cepheus (100): the Double Cluster (99), M39 (95), NGC 7686 (91), the North America Nebula (87), M34 (85) |
| `03-dawn-orion-sirius.jpg` | 05:11 | A | Deep-blue sky before dawn: the Winter Triangle, Canis Major with Sirius, Canis Minor, Orion's Belt (100), the Sword clusters (98-100), M41 (94), M47 (88); trees and power lines below |
| `04-orion-rising.jpg` | 21:57 | A | Orion rising over the trees: the Belt and Sword (99-100), the Pleiades (99), Uranus (100), Aldebaran, Auriga and its clusters, M42 (82, borderline) |
| `05-dawn-jupiter-mars.jpg` | 05:25 | A | Morning twilight: Jupiter and Mars (100) beside the Sickle of Leo (100), only a few stars left |

`annotated/` holds the labeled copies astro-id drew for each frame (constellation stick figures, asterisms in pink,
labels green = high, amber = likely, gray = candidate). `expected.json` is what a run on these files produced:
solved, grade, and the tags at confidence 80 and up. `tags` are asserted by the CI check (confidence >= 85, planets
and the Moon >= 80); `borderline` (80-84) is informational; `scores` records each listed tag's confidence in that run.

## Reproduce

Install astro-id, ASTAP and the W08 star database first (see the main README), then from the repo root:

```powershell
uv run astro-id solve examples/
```

`examples/astro-id.json` supplies the settings: the camera clock was off by `+2559d11h08m`, a 15-minute time
tolerance, timezone America/Los_Angeles and the site (32.6064, -116.3239, 1100 m). Overlays go to
`examples-annotated/` (ignored by git); add `--overlay-dir examples/annotated` to redraw the copies here, or
`--json` for the full result.

2400 px on the long edge is the smallest size astro-id is documented to solve (see
[How it solves](../docs/REFERENCE.md#how-it-solves)); the set is rebuilt with `tools/make_examples.py`
(steps in [docs/TESTING.md](../docs/TESTING.md)).

## Moon photos (`moon/`)

For `astro-id moon` ([Moon features](../docs/REFERENCE.md#moon-features-astro-id-moon)). `moon/annotated/` holds what
it drew. Frames 01-04 are four frames of one lunar eclipse, 2000 x 1333 px, with no EXIF at all (no time, no lens),
so their orientation cannot be computed; the disc is only about 67 px across.

| File | What astro-id finds |
|---|---|
| `01-eclipse-partial.jpg` | disc 67 px (fit quality 1.0), `eclipse-partial` (bright cap at the top, red umbra visible); orientation fit fails (margin 0.18 < 0.25), so no names |
| `02-eclipse-deep.jpg` | disc 68 px, `eclipse-partial` (most of the disc red); fit fails, no names |
| `03-eclipse-totality.jpg` | disc 67 px, `eclipse-partial` (a near-white edge at the upper left keeps it from `eclipse-total`); fit fails, no names |
| `04-eclipse-ending.jpg` | disc 67 px, `crescent`: the umbra is black at this exposure, so it looks like a phase (the message says `--time` tells them apart); fit fails, no names |
| `05-nasa-svs-full-moon.jpg` | disc 690 px, `full`; with `--time 2026-11-24T12:00Z --north-angle 0` (the frame is drawn north up) 83 features at 65-100 (75 of them 80+): Tycho, Copernicus, Plato, Grimaldi, the Apollo sites in place; with no time at all the maria fit finds the pole at 348 deg (true 347.2, certainty 0.64), so the same names at up to 64 |

Reproduce: `uv run astro-id moon examples/moon --to examples/moon/annotated`, and for 05
`uv run astro-id moon examples/moon/05-nasa-svs-full-moon.jpg --time 2026-11-24T12:00Z --north-angle 0 --to examples/moon/annotated`.

## License

Example photos © Ray Salem. All rights reserved. They are included to demonstrate and test astro-id. This covers the JPGs in this folder,
`moon/` frames 01-04, `annotated/`, `moon/annotated/` 01-04 and `docs/img/` (including the demo video and GIF). The
code is MIT (see [LICENSE](../LICENSE)); the MIT license does not cover the photos.

`moon/05-nasa-svs-full-moon.jpg` is not Ray Salem's: it is a frame of *Moon Phase and Libration, 2026* by NASA's
Scientific Visualization Studio (https://svs.gsfc.nasa.gov/5587, frame 7861, 2026-11-24 12:00 UTC), public domain;
credit NASA's Scientific Visualization Studio. Its labeled copy in `moon/annotated/` adds feature names from the
public-domain IAU/USGS Gazetteer of Planetary Nomenclature (see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)).
