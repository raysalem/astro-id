# Example photos

Five frames from one night at a public dark-sky star-party site (Tierra del Sol, California), 7-8 November 2026.
Sony A7R IV, FE 20 mm F1.8 G, f/1.8, 8-10 s, ISO 1000-1600, untracked. Downscaled from 9504 x 6336 and
stripped to the EXIF astro-id needs (time, focal length, exposure, camera and lens model).

| File | Local time | What is in it |
|---|---|---|
| `01-cassiopeia-perseus.jpg` | 20:34 | Cassiopeia's W, Perseus and the Double Cluster (NGC 869/884), the Pleiades at the right edge, Cepheus and the Little Dipper; a power pole on the horizon |
| `02-milky-way-cepheus.jpg` | 20:37 | The autumn Milky Way through Cassiopeia, Cepheus and Lacerta: M39, the Double Cluster, the North America Nebula region |
| `03-taurus-perseus-uranus.jpg` | 20:42 | Perseus to Taurus: the Pleiades, the Double Cluster, Auriga's clusters (M36, M37), Uranus; M31 near the top edge (candidate, 60) |
| `04-orion-rising.jpg` | 21:57 | Orion rising over the trees: the Belt, the Sword and M42, the Pleiades, Aldebaran, Uranus, Auriga and Gemini |
| `05-dawn-jupiter-mars.jpg` | 05:25 | Morning twilight: Jupiter and Mars beside the Sickle of Leo, only a few stars left (grade C solve) |

`annotated/` holds the labeled copies astro-id drew for each frame (constellation stick figures, asterisms in pink,
labels green = high, amber = likely, gray = candidate). `expected.json` is what a run on these files produced:
solved, grade, and the tags at confidence 80 and up (`tags` are asserted; `borderline` 80-84 are informational).

## Reproduce

Install astro-id, ASTAP and the W08 star database first (see the main README), then from the repo root:

```powershell
uv run astro-id solve examples/
```

`examples/astro-id.json` supplies the settings: the camera clock was off by `+2559d11h08m`, a 15-minute time
tolerance, timezone America/Los_Angeles and the site (32.6064, -116.3239, 1100 m). Overlays go to
`examples-annotated/` (ignored by git); add `--overlay-dir examples/annotated` to redraw the copies here, or
`--json` for the full result.

The frames are 2400 to 3200 px on the long edge: the smallest size each one still plate-solves at.
Frame 05 solves at grade C (22 matched stars in a bright sky), which caps Jupiter and Mars near 82.

## License

Photos (the JPGs in this folder, `annotated/` and `docs/img/`) © Ray Salem, CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/). The code stays MIT (see `LICENSE`).
