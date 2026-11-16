# Plan: fair quality grades and cull verdicts for twilight frames (B-E3)

Goal: twilight frames that plate-solve well are not graded C or culled for "too few stars". Branch `twilight-cull`
(from main 8c8ee5c, main 9833267 merged in). Started 2026-11-15. Status: done 2026-11-16, merged into main
(one open item: frame-7620, below).

## Big picture
1. Project: astro-id, a plate-solving and sky-labeling tool and Facet add-on.
2. Task: engine quality metrics and culling ([ISSUES](../ISSUES.md) B-E3, [TODO](../TODO.md) "Engine").
3. Touches: `astro_id/quality.py`, `astro_id/cull.py`, an opt-in flag in `astro_id/stars.py`, their tests, the
   quality/culling docs. No solve, label or Facet changes.
4. Nearby: `label-placement` (labels; no overlap with these files).
5. Risk: the quality numbers of every frame move (QUALITY_VERSION 2); dark-sky verdicts must not.

## Problem

`quality.py` grades the twilight examples 03 and 05 C (39 stars; "1 stars, HFD None"), although ASTAP solves both at
grade A with 1472 and 924 matched stars. In a session of the five examples the cull rule marks 05 `reject` ("only 1
detected") and 03 `review`.

## Cause (measured on the five 2400 px examples)

1. **Background mesh misaligned.** `stars.background_map` upsamples the per-cell medians with `ndi.zoom(order=1)`,
   which pins grid point 0 to pixel 0 and the last grid point to the last pixel. Each median belongs to its cell
   centre, so the mesh is shifted by up to half a cell. On a sky ramp this leaves a ramp residual of up to
   gradient x half a cell. On a twilight sky that residual is larger than a faint star. The smoothed-noise estimate
   then measures the ramp, and the 5 sigma threshold lands above almost every star. Residual (sigma of the residual
   smoothed 8 px): 05 1.56, 03 1.77, dark frames 0.7-0.9. With centre-aligned interpolation: 05 0.13, 03 0.39.
2. **Halving a small copy.** B-E1 stopped the solve from halving images under 6000 px (`solve.reduce_for`).
   `quality.py` still halves, so its finest kernel (sigma 2 half px, about 9 px FWHM at full size) is too coarse for
   the 2-5 px stars of a 2400 px copy. Aligned background, sm 2: 05 finds 146 stars at half size and 792 at full
   size (ASTAP matched 924); 03 finds 599 and 3412.
3. **Solve evidence ignored.** `grade()` and the `stars` cull check read only `detected`. A solve with hundreds of
   matched stars proves the stars are there.

Ruled out: the noise estimator (MAD vs lower-half MAD changes counts by under 20%), and the luminance conversion
(L beats R, G and B on every frame; on 05 blue is clipped).

## Fix

1. `stars.background_map(img, cell, aligned=False)` and `detect(..., aligned=False)`. With `aligned=True` the cell
   medians are interpolated from their cell centres (`map_coordinates`, edge-clamped). The default keeps the solve
   byte-identical; quality passes `aligned=True`. Aligning the solve's own detection is a separate follow-up
   (it would move `expected.json`).
2. `quality.frame_quality` works at `solve.reduce_for(w, h)` (the same rule as the solve): full size under 6000 px,
   half from 6000 px. 61 MP frames are unchanged in scale. `measure_arrays(..., reduce=REDUCE)` takes the factor,
   with the default unchanged for callers that pass a half-size image.
3. Star evidence = `max(detected, matched)`. `grade()` uses it, and `frame_quality` re-grades after `matched` is
   set. The cull `stars` check and the session reference use the same number, so a frame and its baseline stay
   comparable. `detected` stays in the metrics as measured, and the reason text names both counts.
4. `QUALITY_VERSION` becomes 2 (the numbers mean something different). The docs say "work image (same scale as the
   solve)" where they said "half-size image".

## Found during the analytical review (61 MP frames, scratch copies of the B-E1 test set)

5. **Scale choice fooled by clumps.** With the cleaner background, a coarse scale on a sharp Milky Way frame
   (frame-7614) measured clumps (HFD 27-66 px) that fit its kernel better than the 6 px stars fit the fine one, and
   the frame went to `review` for focus. A first guard (coarse scales must agree in size with the fine one)
   broke frame-7612, whose ~85 px discs read 15 px from rim fragments at sm 2 (found re-measuring all 18 frames).
   `pick_scale` now asks whether the finest well-supported scale's stars are compact (HFD <= 1.35x its kernel
   FWHM; sharp frames 0.68-1.21, fragments >= 1.55): if so it is used, else the v1 matched filter decides.
6. **Open: frame-7620 (moonlit, Moon and trees in frame)** reads HFD 11.4 instead of 8.7: the cleaner mesh admits
   sky-through-foliage blobs into the brightest-150 set (stars common to both versions: 7.3 vs 7.0 px). Tried and
   dropped (no effect, or disturbed other frames): an aperture-isolation test, ranking by peak, a footprint gate.
   Follow-up: a foreground mask.

## Tests

- Ramp: `background_map(aligned=True)` leaves under 0.05 DN on a linear ramp; the default is unchanged.
- Synthetic twilight frame (2400 x 1600, steep sky ramp, faint stars): enough stars detected, grade not C for star
  count, verdict not `reject`.
- Synthetic trailed (2:1, 3:1) and defocused (disc) frames at full size (reduce 1): still elongated or soft, still
  `review` or `reject` in a session with sharp frames.
- `grade` and `cull_rank` use `matched` when it is larger; an unsolved frame (`matched` None) is unchanged.
- Existing `test_quality.py` and `test_cull.py` pass unchanged (or a changed assertion is explained).
- Examples (ASTAP): 03 and 05 are not graded C for stars, not `reject`. The dark-only session (01, 02, 04) keeps its
  verdicts (01 `review` for focus, 02 and 04 `keep`).

## Verify

`uv run pytest -q`, `node --test` (both files), `uv run python tools/check_docs.py`, `python dev-notes/tools/check.py`,
the example before/after table in [ISSUES](../ISSUES.md) B-E3, then push `twilight-cull` and run `ci.yml` on it.
No merge into main.

   All 18 frames of the night (full size, quality only, session of 18; main -> fix): decisions change on 4.
   - frame-7620: keep -> review (focus 1.7x). This is the only regression.
   - frame-7625: review -> keep (twilight star count).
   - frame-7617: review -> keep (focus 1.52x -> 1.48x, on the 1.5x line).
   - frame-7615: review either way (a black frame), but for stars only; its hot-pixel trailing flag is gone.
   The other 14 keep their decision. frame-7612 reads 84.5 px at sm 16 (v1: 84.0); frame-7614 reads 6.39 (= v1).
   Other bright-sky frames drift like 7620 but stay `keep`: frame-7618 HFD 7.8 -> 9.3, frame-7622 5.2 -> 7.0.

## Review
- dev-notes-reviewer, changes needed, 2026-11-16, attempt 1. Code and tests sound; docs to fix. Fixes:
  DETAILS quality section rewritten for v2 (v1 kept as history); Decision row for the per-region elongation;
  Gotcha on v1/v2 numbers not being comparable, with a `cull_session` warning for mixed versions (and a test);
  REFERENCE trailing wording; this Verify section; the remaining 10 night frames re-measured, which found the
  frame-7612 defocus regression and replaced the size-agreement guard with the compactness rule (test with
  7612's numbers); a hollow-defocus (donut) test.
- Owner OK: Ray, 2026-11-16, both Decisions approved.
- dev-notes-reviewer, changes needed, 2026-11-16, attempt 2: code sound, attempt-1 findings fixed; two doc
  lines. Fixes: main (label placement) merged in, DETAILS Decisions and Gotchas from both kept, TODO re-measure
  sentence dropped (done), TESTING test count, the COMPACT caveat (empirical, ~6.4 work px absolute cut,
  oversampled frames fall back to v1, depends on sm 2 support) in DETAILS and quality.py, fragment ratio
  reconciled to 1.55 (1.49 was frame-7630 as main measured it).
- Owner accepted the frame-7620 keep->review regression, 2026-11-16.

