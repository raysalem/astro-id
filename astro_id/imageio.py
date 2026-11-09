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
    return ExifInfo(dt, _rational(sub.get(37386)), _rational(sub.get(41989)), w, h, _rational(sub.get(33434)))


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
    arr = load_gray(path, 1)
    return ExifInfo(dt, fl, f35, arr.shape[1], arr.shape[0], et)


def load_gray(path: Path, reduce: int = 1) -> np.ndarray:
    """Load as float32 grayscale; `reduce` is an integer downscale (JPEG uses DCT scaling)."""
    if path.suffix.lower() in RAW_EXT:
        import rawpy

        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16, half_size=reduce >= 2)
        a = rgb.astype(np.float32).mean(axis=2) / 256.0
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
