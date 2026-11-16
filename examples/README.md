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

## Brief
Test input and README pictures: the five sky frames (settings in `astro-id.json`, results in `expected.json`) and four NASA Moon frames in [moon/](#moon-photos-moon).

## Tests
- CI job `examples`: solves this folder, checks it with `tests/check_examples.py`, then runs `tests/test_moon.py`. Steps: [Reproduce](#reproduce). Needs ASTAP + W08.

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

For `astro-id moon` ([Moon features](../docs/REFERENCE.md#moon-features-astro-id-moon)). These are four frames of
NASA's *Moon Phase and Libration, 2026* (SVS ID 5587), 730 x 730 px, each drawn celestial-north up, so a run with
the frame's time and `--north-angle 0` has the true orientation and the result can be checked by eye.
`moon/annotated/` holds what astro-id drew.

| File | Frame, time (UTC) | Disc | What astro-id finds |
|---|---|---|---|
| `05-nasa-svs-full-moon.jpg` | 7861, 2026-11-24 12:00 | 690 px | `full`; 83 features at 65-100 (75 of them 80+): Tycho, Copernicus, Plato, Mare Imbrium, Mare Crisium, the Apollo sites in place. With no time at all the maria fit finds the pole at 348 deg (true 347.2, certainty 0.64), so the same names at up to 64 |
| `06-nasa-svs-waxing-crescent.jpg` | 7573, 2026-11-12 12:00 | 620 px | `crescent`; 11 features, 6 at 80+ (Mare Undarum, Mare Spumans, Petavius, Furnerius, Vendelinus), all on the lit limb. The weakest of the four: a thin crescent is only a little of the disc, and the dark side is not labeled |
| `07-nasa-svs-first-quarter.jpg` | 7693, 2026-11-17 12:00 | 631 px | `half`; 51 features, 38 at 80+: Mare Serenitatis, Tranquillitatis, Crisium, Fecunditatis, Nectaris, Montes Caucasus, Apollo 11 and 17 (the lit half only; a few names sit on the terminator at lower confidence) |
| `08-nasa-svs-waxing-gibbous.jpg` | 7759, 2026-11-20 06:00 | 656 px | `gibbous`; 72 features, 62 at 80+: Tycho, Copernicus, Plato, Mare Crisium (100), Mare Imbrium (89), Apollo 11-17 |

Reproduce (the time of each frame is hour N-1 after 2026-01-01 00:00 UTC):

```powershell
uv run astro-id moon examples/moon/05-nasa-svs-full-moon.jpg --time 2026-11-24T12:00Z --north-angle 0 --to examples/moon/annotated
uv run astro-id moon examples/moon/06-nasa-svs-waxing-crescent.jpg --time 2026-11-12T12:00Z --north-angle 0 --to examples/moon/annotated
uv run astro-id moon examples/moon/07-nasa-svs-first-quarter.jpg --time 2026-11-17T12:00Z --north-angle 0 --to examples/moon/annotated
uv run astro-id moon examples/moon/08-nasa-svs-waxing-gibbous.jpg --time 2026-11-20T06:00Z --north-angle 0 --to examples/moon/annotated
```

These are not real camera photos: they show the method on clean, known frames. A real photo needs the same disc
size (about 250 px for craters) and a time; see the [manual](../docs/MANUAL.md#photos-of-the-moon-astro-id-moon).

## License

Example photos © Ray Salem. All rights reserved. They are included to demonstrate and test astro-id. This covers the JPGs in this folder,
`annotated/` and `docs/img/` (including the demo video and GIF). The
code is MIT (see [LICENSE](../LICENSE)); the MIT license does not cover the photos.

The four `moon/0*-nasa-svs-*.jpg` frames and their copies in `moon/annotated/` are not Ray Salem's: they are frames of
*Moon Phase and Libration, 2026* by NASA's Scientific Visualization Studio (https://svs.gsfc.nasa.gov/5587; frames
7861, 7573, 7693 and 7759 = 2026-11-24 12:00, 2026-11-12 12:00, 2026-11-17 12:00 and 2026-11-20 06:00 UTC), public
domain ("All of our content is in the public domain", https://svs.gsfc.nasa.gov/help/); credit NASA's Scientific
Visualization Studio. The labeled copies add feature names from the public-domain IAU/USGS Gazetteer of Planetary
Nomenclature (see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)).
