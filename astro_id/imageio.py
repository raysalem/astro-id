"""Image loading, EXIF reading and RAW -> same-stem JPG mapping."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ExifTags

Image.MAX_IMAGE_PIXELS = None
log = logging.getLogger("astro_id")

DIRECT_EXT = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}
RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw", ".nrw", ".3fr", ".iiq"}


def select_inputs(paths: list[Path]) -> list[Path]:
    """Pick one file per (folder, stem): a direct image beats its RAW twin.

    RAW files are kept only when no same-stem JPG/TIF/PNG exists next to them.
    """
    chosen: dict[tuple[str, str], Path] = {}
    for p in sorted(paths):
        ext = p.suffix.lower()
        if ext not in DIRECT_EXT and ext not in RAW_EXT:
            continue
        key = (str(p.parent).lower(), p.stem.lower())
        cur = chosen.get(key)
        if cur is None:
            chosen[key] = p
        elif cur.suffix.lower() in RAW_EXT and ext in DIRECT_EXT:
            chosen[key] = p
        elif cur.suffix.lower() in DIRECT_EXT and ext in DIRECT_EXT:
            # prefer jpg over tif/png for speed, deterministic otherwise
            if ext in (".jpg", ".jpeg") and cur.suffix.lower() not in (".jpg", ".jpeg"):
                chosen[key] = p
    return sorted(chosen.values(), key=lambda q: (str(q.parent).lower(), q.name.lower()))


def collect(target: Path) -> list[Path]:
    if target.is_dir():
        return select_inputs([p for p in target.iterdir() if p.is_file()])
    if target.suffix.lower() in RAW_EXT:
        for ext in (".JPG", ".jpg", ".JPEG", ".jpeg"):
            twin = target.with_suffix(ext)
            if twin.exists():
                return [twin]
    return [target]


@dataclass
class ExifInfo:
    datetime_original: datetime | None
    focal_mm: float | None
    focal35_mm: float | None
    width: int
    height: int
    exposure_s: float | None
    lens: str | None = None


def _rational(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def read_exif(path: Path) -> ExifInfo:
    if path.suffix.lower() in RAW_EXT:
        return _read_exif_raw(path)
    with Image.open(path) as im:
        w, h = im.size
        ex = im.getexif()
        sub = ex.get_ifd(ExifTags.IFD.Exif) if ex else {}
    dto = sub.get(36867) or sub.get(36868) or ex.get(306)
    dt = None
    if dto:
        try:
            dt = datetime.strptime(str(dto).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            dt = None
    lens = sub.get(42036)
    lens = str(lens).strip("\x00 ") or None if lens else None
    return ExifInfo(dt, _rational(sub.get(37386)), _rational(sub.get(41989)), w, h, _rational(sub.get(33434)), lens)


def _read_exif_raw(path: Path) -> ExifInfo:
    # Pillow can read the TIFF-based EXIF of most RAW formats.
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            sub = ex.get_ifd(ExifTags.IFD.Exif)
            dto = sub.get(36867)
            dt = datetime.strptime(str(dto), "%Y:%m:%d %H:%M:%S") if dto else None
            fl, f35, et = _rational(sub.get(37386)), _rational(sub.get(41989)), _rational(sub.get(33434))
    except Exception:
        dt = fl = f35 = et = None
    import rawpy

    with rawpy.imread(str(path)) as r:
        w, h = r.sizes.iwidth, r.sizes.iheight
    return ExifInfo(dt, fl, f35, w, h, et)


def raw_twin(jpg: Path) -> Path | None:
    """The same-stem RAW next to ``jpg`` (any case), or None."""
    if jpg.suffix.lower() in RAW_EXT:
        return None
    try:
        for q in jpg.parent.iterdir():
            if q.stem.lower() == jpg.stem.lower() and q.suffix.lower() in RAW_EXT:
                return q
    except OSError:
        pass
    return None


def raw_crop_for(raw: Path, width: int, height: int):
    """(left, top, w, h) of the part of the decoded RAW that is the (width x height) JPG frame, from the
    camera's crop (Sony A7R IV: 9568x6376 decoded area -> 9504x6336 JPG, centered: 32 px left, 20 px top), or None
    when the mapping is not certain (different sizes and no matching camera crop)."""
    try:
        import rawpy

        with rawpy.imread(str(raw)) as r:
            s = r.sizes
            if (s.iwidth, s.iheight) == (width, height):
                return (0, 0, width, height)
            if (s.crop_width, s.crop_height) == (width, height) and width <= s.iwidth and height <= s.iheight:
                # rawpy reports left margin 0, but the Sony JPG is the CENTERED crop of the decoded area
                # (measured on an A7R IV: 32 px left, 20 px top = (9568-9504)/2, (6376-6336)/2)
                return ((s.iwidth - width) // 2, (s.iheight - height) // 2, width, height)
    except Exception as exc:
        log.debug("raw crop lookup failed for %s: %s", raw, exc)
    return None


def choose_source(path: Path, exif, solve_source: str):
    """(pixel source file, crop) for solving ``path``: its RAW twin when ``solve_source`` asks for it and the
    RAW maps onto the JPG frame with certainty, else the file itself. Results stay in ``path``'s pixel space."""
    if solve_source == "jpg" or path.suffix.lower() in RAW_EXT:
        return path, None
    twin = raw_twin(path)
    if twin is None:
        if solve_source == "raw":
            log.info("%s: no RAW twin, solving the JPG", path.name)
        return path, None
    crop = raw_crop_for(twin, exif.width, exif.height)
    if crop is None:
        log.warning("%s: cannot map the RAW onto the JPG frame with certainty, solving the JPG", path.name)
        return path, None
    return twin, crop


def load_gray(path: Path, reduce: int = 1, crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Load as float32 grayscale; `reduce` is an integer downscale (JPEG uses DCT scaling).

    RAW: linear demosaic (gamma 1, no auto-bright, camera white balance), channel mean, so star peaks are
    not clipped; ``crop`` = (left, top, width, height) in full-resolution pixels."""
    if path.suffix.lower() in RAW_EXT:
        import rawpy

        half = reduce >= 2
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16, half_size=half,
                                  gamma=(1, 1))
        a = rgb.astype(np.float32).mean(axis=2) / 256.0
        if crop is not None:
            k = 2 if half else 1
            l, t, w, h = crop
            a = a[t // k:(t + h) // k, l // k:(l + w) // k]
        step = reduce // 2 if reduce >= 2 else reduce
        if step > 1:
            h, w = (a.shape[0] // step) * step, (a.shape[1] // step) * step
            a = a[:h, :w].reshape(h // step, step, w // step, step).mean(axis=(1, 3))
        return a
    with Image.open(path) as im:
        w, h = im.size
        if reduce > 1 and im.format == "JPEG":
            im.draft("L", (w // reduce, h // reduce))
        g = im.convert("L")
        if g.size != (w // reduce, h // reduce):
            g = g.resize((w // reduce, h // reduce), Image.BOX)
        return np.asarray(g, dtype=np.float32)


def load_rgb_preview(path: Path, width: int = 3000) -> Image.Image:
    if path.suffix.lower() in RAW_EXT:
        import rawpy

        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, half_size=True)
        im = Image.fromarray(rgb)
    else:
        im = Image.open(path)
        w, h = im.size
        if im.format == "JPEG":
            im.draft("RGB", (width, int(h * width / w)))
        im = im.convert("RGB")
    w, h = im.size
    if w > width:
        im = im.resize((width, round(h * width / w)), Image.LANCZOS)
    return im
