# Plan: Moon features (`astro-id moon`)

Status: **first version built 2026-11-15.** The plan below was written before any code; where the build departed
from it, "What was built" says what changed and why. "What is left" lists the next steps.

## Goal

On a photo of the Moon, name its geography: maria and oceans, the largest craters, mountain ranges and the Apollo
landing sites. Each name is drawn on a labeled copy of the photo and listed as an object tag with a 0-100
confidence, the same way sky labels work for a plate-solved frame. Owner's words: "add plating for the Moon to see
its geography... would require Moon zoom in."

## What the user sees

```powershell
uv run astro-id moon <photo-or-folder> [--time ISO] [--site lat,lon] [--north-angle DEG] [--mirror] [--to DIR] [--json]
```

- A labeled copy `<name>_moon.jpg` (next to the photo, or in `--to DIR`): the fitted limb as a thin circle, a
  legend line (disc size, phase or eclipse state, how the orientation was found), and the feature names in the
  usual tier colours (green high, amber likely, gray candidate).
- A one-line summary per photo on stderr, e.g. `4.jpg: Moon 66 px across, partial eclipse; orientation unknown;
  features need about 250 px (craters) / 60 px (maria)`.
- `--json`: one document per run, `{"contract": 1, "moon": [...]}`; per photo the disc (centre, radius, fit RMS,
  arc coverage), the state (`full`, `gibbous`, `half`, `crescent`, `eclipse-partial`, `eclipse-total`), the
  orientation (`north_angle_deg`, `mirrored`, `method`, `certainty`), the features (name, tag, kind, lat/lon,
  pixel x/y, size in px, confidence) and the annotations payload (ANNOTATIONS.md v1, one new group `moon`).
- When the disc is too small: no names, and a clear message: "the Moon is N px across here; maria need about
  60 px, the large craters about 250 px".

## Inputs and limits

- One photo with the Moon in it, JPG/TIF/PNG (RAW via its same-stem JPG, as elsewhere). The Moon need not be
  centred; it must be whole in the frame for this first version.
- Time and site are optional. With both, the orientation is computed; without them it is fitted from the picture
  (needs enough pixels) or given with `--north-angle`.
- A disc of about 65 px (a wide frame of an eclipse, 2000 x 1333, no EXIF time) is at the very bottom of what can
  work: at 65 px a 100 km crater is under 2 px, so **no crater can be named**; the largest maria are 10-20 px, and a
  67 px disc is too small (craters like Tycho need about 245 px). Without a time the orientation cannot be
  computed; a fit is tried and its result is reported honestly. An eclipsed or black-shadowed disc needs the
  photo's time to be told from a phase.
- What to shoot for crater names: the disc in pixels is about `focal length (mm) x 9.1 / pixel pitch (um)`
  (the Moon is ~0.52 deg). A crater is nameable at ~6 px, i.e. a 85 km crater (Tycho) needs a ~250 px disc; ~40 km
  craters (Aristarchus, Kepler) need ~550 px. Example: 3.76 um pixels (Sony A7R IV) give 250 px at ~105 mm and
  550 px at ~230 mm, at full resolution (not a 2000 px downscale).

## Method

1. **Find the disc.** Gray image, background level and noise from the frame's median/MAD. Threshold at several
   levels between the noise floor and the brightest part (the eclipsed part is dim and red, the lit part bright),
   take the largest blob, extract its boundary pixels, and fit a circle with RANSAC (3-point circles, inliers within
   1.5 px) plus a least-squares refinement on the inliers. A candidate is penalised for blob pixels that fall
   outside it, so a crescent's terminator arc (concave) or a gibbous one (an ellipse arc, not a circle) loses to the
   true limb. Output: centre, radius, RMS of the inliers, and the arc coverage (fraction of the 360 deg of the
   circle that has limb points). Quality `q_disc` = coverage-weighted, RMS-penalised, in 0..1.
2. **Lit part and state.** Lit mask = pixels above half the lit level; lit fraction = lit area / disc area. The
   unlit part of a normal phase is black (earthshine is ~1e-4 of the sunlit Moon); in an eclipse the umbra is dim
   but clearly above the sky and red. So: unlit part brighter than ~3 % of the lit level and red (R/G > 1.15) =
   eclipse (`eclipse-total` when nothing is above the lit threshold relative to the red level, else
   `eclipse-partial`); otherwise phase by lit fraction (full > 0.97, gibbous > 0.55, half > 0.4, else crescent).
3. **Orientation** (direction of celestial north on the image), sources in order:
   1. `--north-angle DEG` (and `--mirror` for a star diagonal): certainty 1.0.
   2. Plate solve of the same frame: not in the first version (a Moon exposure shows no stars; noted as later).
   3. **Time + site, camera held level:** the parallactic angle `q` of the Moon (skyfield, already a dependency;
      DE421 already downloaded by `astro-id setup`). With the camera level, image up = zenith, so celestial north
      is at `-q` from up. The camera roll is an *assumption*, stated in the legend; certainty 0.75. Time alone is
      not enough (the parallactic angle needs the site).
   4. **Albedo fit:** render a low-resolution albedo model of the near side (maria dark, highlands bright; the maria
      as discs at their catalog centres and sizes) for each rotation angle (1 deg steps) and both mirror states,
      with the libration of the time if known (else 0, 0), and correlate it with the photo's disc after removing
      the illumination gradient (divide by a heavily blurred copy, so a phase or the Earth's shadow does not drive
      the fit). Certainty from the peak: its correlation and its margin over the best angle more than 30 deg away.
      Needs the disc to be at least ~40 px across, else it is not attempted.
   5. None: the disc and the state are reported, no feature names.
4. **Libration and the axis** (the Moon's physical ephemeris, Meeus, *Astronomical Algorithms* ch. 53): from the
   Moon's apparent geocentric ecliptic longitude/latitude (skyfield), nutation and obliquity: the sub-Earth point
   (optical libration `l`, `b`) and the position angle `P` of the Moon's north pole. Physical libration (< 0.04 deg)
   is ignored. Checked against Meeus' worked example 53.a and against NASA SVS "Moon Phase and Libration 2026"
   values for two dates. Topocentric libration differs by up to ~1 deg; ignored in this version (stated).
5. **Projection:** a feature at selenographic (lat, lon) is projected orthographically for the sub-Earth point,
   rotated by `P` and then by the image's north angle (east is 90 deg counter-clockwise of north on an unmirrored
   picture), scaled by the fitted radius. Visible only when on the near hemisphere.
6. **Which features:** size on the image = diameter x radius_px / 1737.4 km x foreshortening (`cos` of the angle
   from the disc centre). Labeled only when that size is at least 6 px (maria and craters; mountain ranges use
   their length; Apollo sites use a 40 km nominal "where to look" size, so they appear only on large discs), and
   only on the visible part of the disc: the lit mask for a phase (the terminator comes out of the photo itself);
   in an eclipse the umbra still shows the surface, so features there are kept with a lower illumination factor.
7. **Confidence:** `100 x q_disc x q_orient x q_size x q_illum`, where `q_size` = 1 - exp(-(size_px / 6)^2) style
   ramp (0.63 at 6 px, ~1 from 12 px), `q_illum` = share of the feature's footprint inside the lit mask (eclipse
   umbra counted at 0.6), `q_orient` = the orientation certainty above. Tiers as everywhere: high >= 80, likely
   65-79, candidate < 65.
8. **Output:** the annotations payload (ANNOTATIONS.md v1) with a new group `moon` ("Moon features", on) and
   `point`/`circle` items, so `astro_id.export.render` draws the labeled copy exactly like sky labels; object tags
   are the lower-case names (`mare imbrium`, `tycho`, `apollo 11`), or `moon/mare imbrium` with `--tag-style kind`.

**Why a new command, not automatic in `label`/`solve`:** a Moon frame has no stars, so `solve` fails on it by
design; adding a branch inside the solve pipeline now would touch the files that the release merge is changing in
parallel. A separate `astro-id moon` is purely additive. Later: `label`/`solve` call `moon` when a solve fails and
a disc of more than ~200 px is found (listed under "What is left").

## Data sources and licenses

| Data | By | License | How |
|---|---|---|---|
| Lunar feature names, centres, diameters (curated ~80) | IAU WGPSN / USGS Astrogeology, *Gazetteer of Planetary Nomenclature* (`MOON_nomenclature_center_pts`) | public domain (the shapefile metadata states "Use constraints: Public domain."; USGS works are U.S. public domain) | vendored as facts in `astro_id/moon_catalog.py`, rebuilt by `tools/make_moon_catalog.py` |
| Apollo landing-site coordinates | NASA NSSDCA (Lunar Module positions, Wagner et al. 2017) | U.S. public domain | vendored (6 rows) |
| Moon position, nutation | JPL DE421 via skyfield | as today | already downloaded |
| High-quality test image (5th) | NASA Scientific Visualization Studio, *Moon Phase and Libration, 2026* (ID 5587; LRO LOLA + LROC WAC, rendered) | public domain ("All of our content is in the public domain unless otherwise noted", SVS help page) | committed, 730 px, credited |

Image 8 that Ray pasted is the Wikimedia Commons file *FullMoon2010.jpg* by Gregory H. Revera (verified on its
Commons page: own work, 22 Oct 2010, CC BY-SA 3.0 / GFDL 1.2+, 960 px is a Commons thumbnail). Share-alike would
attach to every labeled copy drawn from it, so it is **not committed**; the NASA SVS frame replaces it (it also
comes with exact time, libration and axis angle, i.e. ground truth). Image 8 is still used locally as a run.

## Files

- New: `astro_id/moon.py` (disc fit, state, ephemeris maths, projection, albedo fit, features, payload, CLI run),
  `astro_id/moon_catalog.py` (the curated catalog), `tools/make_moon_catalog.py`, `tests/test_moon.py`,
  `examples/moon/` (four NASA frames, notes in examples/README.md), this plan.
- Changed, minimally: `astro_id/cli.py` (register the subcommand), `astro_id/vocab.py` + `CONTRACT.md` (exit codes
  of `moon`, the help test keeps them in step), `docs/MANUAL.md` and `docs/REFERENCE.md` (a "Moon" section),
  `THIRD_PARTY_NOTICES.md` (gazetteer, NASA SVS image), `examples/README.md` (the Moon frames and credits),
  README only if the result is worth showing.

## Tests (no network)

- Synthetic discs: full, gibbous, half, crescent, a partial arc cut by the frame edge... (centre and radius within
  1 px), a red eclipse disc (state `eclipse-partial` / `eclipse-total`), a blank frame (no disc).
- Ephemeris maths: Meeus example 53.a (l = -1.206, b = 4.194, P = 15.08 deg) from its own inputs; the full chain
  against NASA SVS values for 2026-11-24 12:00 UTC and 2026-11-20 06:00 UTC (skipped when DE421 is not cached).
- Projection: zero libration and zero angles put (0, 0) at the centre, Mare Crisium (east limb) on the image's
  right for north up and on the left when mirrored; a known libration moves a limb feature in or out of view.
- Parallactic angle: 0 on the meridian, sign east/west.
- Albedo fit: a synthetic disc rendered from the model at a known angle is recovered within a few degrees.
- Feature selection: a 65 px disc labels no craters; a 700 px disc labels Tycho and Copernicus; the message for a
  small disc.
- Payload passes the same shape rules as the sky payload; CLI exit codes.

## What was built (2026-11-15, first version)

- `astro-id moon <photo-or-folder>` (new subcommand; `astro_id/moon.py`, `astro_id/moon_catalog.py`). Not wired
  into `solve`/`label` or the Facet add-on yet.
- **Changes from the plan, with reasons:**
  - The disc is refitted after RANSAC to the half-level crossing of rays from the centre: the threshold blobs sat
    1-2 px out in the blur (a synthetic 60 px disc measured 63) or inside a dim eclipsed edge (an eclipse frame came
    out 62 px and off-centre; refitted 67 px, centred).
  - "Sunlit" is above 22 % of the bright level, not 50 %: at 50 % the maria counted as unlit and a full Moon read
    as gibbous.
  - The albedo fit flattens the sunlit part and the visible umbra **separately** and skips a band along the
    shadow edge. With one blur over the whole disc, a synthetic eclipse shadow sent the fit off by 20-170 deg; with
    regions it was right in all 25 cases.
  - The fit's certainty is the relative margin of its peak over the best angle more than 30 deg away (0.25 -> 0,
    0.5 -> 0.9). Measured on NASA SVS frames turned by 0/70/145/220/300 deg and shrunk to 650/300/150/100/65 px:
    full Moon 25/25 right within 5 deg, full Moon with a synthetic red umbra 25/25, gibbous 24/25 (one 9 deg off);
    every wrong answer (gibbous plus a shadow, which cannot happen in nature) had a margin under 0.28.
  - The fit searches only the unmirrored picture (a camera lens does not mirror); `--mirror` flips it. Searching
    both doubled the false peaks.
  - A phase lights exactly half the limb; a lit limb under 140 or between 230 and 320 deg is a shadow edge. With
    `--time`, a partial disc when the Sun lights the whole Moon is an eclipse.
  - Maria and mountain ranges are drawn as a mark at the centre, craters as their rim to scale (a mare is not a
    circle). Names under 20 are kept in the JSON, not drawn. A small Moon is drawn on a 4x crop.
- **Ephemeris check:** Meeus example 53.a exact to 0.002 deg (P 15.066 vs 15.08 with physical libration); NASA SVS
  2026-11-24 12:00 and 2026-11-20 06:00 UTC: libration within 0.03 deg, axis angle within 0.12 deg.
- **Real runs** (`examples/README.md` has the table): four eclipse frames of 67-68 px (not committed): the disc was
  found (quality 1.0), three read as `eclipse-partial` (red umbra visible), one as `crescent` (its umbra was black; without a
  time a shadow edge and a phase look alike at this size). The maria fit failed on all four (margin 0.005-0.18):
  no names, and the message says what is missing. NASA SVS full Moon (690 px): with its time and north up,
  83 features at 65-100 in the right places; with nothing at all, the fit finds the pole at 348 deg (true 347.2).
  A Commons full-Moon photo (run locally, not committed): fit at 344 deg, 90 % certainty, labels on
  Tycho, Copernicus, Plato, Grimaldi and Crisium in place.
- **Merge into the release (2026-11-15):** the lit level is the 99th percentile (was 97th) so a rendered earthshine
  is not counted as sunlit on a crescent, and a time that says the Moon is under 90 % lit makes a narrow lit limb a
  thin crescent rather than the Earth's shadow edge. Examples are now four NASA SVS frames (full, waxing crescent,
  first quarter, waxing gibbous).
- Tests: `tests/test_moon.py` (30, no network; the two NASA SVS ephemeris checks and the time + site check skip
  without a cached DE421).
- A raw EXIF time (no `clock_offset`, no `--time`) lowers the time + site certainty from 0.75 to 0.5, as `solve`
  distrusts an unconfirmed clock.

## What is left

1. A real Moon photo at 250+ px with EXIF time and site, to check the time + site path on a real camera.
2. Eclipse and thin-crescent handling (both need the photo's time).
3. Run automatically from `label`/`solve` when a solve fails and a disc over ~200 px is found; the Facet add-on.
4. Topocentric libration (up to ~1 deg) and physical libration (~0.04 deg); a plate solve for wide frames with the
   Moon and stars; mare outlines instead of caps for the albedo model; a crop of a disc cut by the frame edge.
5. A finer albedo reference (e.g. a downsampled LROC WAC mosaic, public domain) if the cap model is not enough on
   real photos.

## Open questions (for Ray)

1. Own Moon photos at a 250 px disc or more, with their time (EXIF or `--time`) and site.
2. Is a hand-held level camera the usual set-up for Moon shots, or a telescope with a star diagonal (mirrored)?
