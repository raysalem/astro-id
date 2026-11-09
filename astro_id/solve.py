"""Plate solving: ASTAP on a central crop, then a full-frame TAN-SIP refit against Hipparcos.

Fallback (only when ASTAP fails and ASTROMETRY_API_KEY is set): nova.astrometry.net.
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import fit_wcs_from_points
from PIL import Image
from scipy.spatial import cKDTree

from . import data
from .stars import Detections, detect, fwhm_estimate

log = logging.getLogger("astro_id")
warnings.simplefilter("ignore", FITSFixedWarning)

REDUCE = 2  # detection / crop work happens on a 1/2-scale image
SMOOTH_SCALES = (1.0, 3.0, 6.0, 12.0)  # gaussian sigma in reduced pixels
CORNER_GROWTH = 0.0  # optional extra match radius toward the corners (0: corners are handled by ring growth)
RING_MAG, RING_RADIUS, RING_MAX_DET = 5.5, 15.0, 400  # corner growth: bright stars, radius px @3000 wide


# ---------------------------------------------------------------- ASTAP discovery
def find_astap() -> tuple[Path | None, Path | None]:
    """Return (executable, database dir). ASTAP_PATH may point at the exe or its folder."""
    cands: list[Path] = []
    env = os.environ.get("ASTAP_PATH")
    if env:
        p = Path(env)
        cands += [p] if p.suffix.lower() == ".exe" or p.is_file() else [p / "astap_cli.exe", p / "astap.exe",
                                                                        p / "astap_cli", p / "astap"]
    for base in [os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]:
        if base:
            cands += [Path(base) / "astap" / "astap_cli.exe", Path(base) / "astap" / "astap.exe"]
    for name in ("astap_cli", "astap"):
        w = shutil.which(name)
        if w:
            cands.append(Path(w))
    cands += [Path("/opt/astap/astap_cli"), Path("/opt/astap/astap")]
    exe = next((c for c in cands if c.is_file()), None)
    if exe is None:
        return None, None
    dbs = [os.environ.get("ASTAP_DB"), str(exe.parent)]
    db = next((Path(d) for d in dbs if d and any(Path(d).glob("*_0101.*"))), None)
    return exe, db


def available_databases(db: Path | None) -> set[str]:
    if not db:
        return set()
    return {p.name.split("_")[0].lower() for p in db.glob("*_0101.*")}


# ---------------------------------------------------------------- results
@dataclass
class Solution:
    solved: bool
    solver: str | None = None
    wcs: WCS | None = None
    width: int = 0
    height: int = 0
    matched: int = 0
    rms_px: float | None = None
    grade: str | None = None
    lim_mag: float | None = None
    psf_px: float = 4.0
    error: str | None = None
    det: Detections | None = None  # full-res coordinates
    match_xy: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    match_resid: np.ndarray = field(default_factory=lambda: np.zeros(0))
    match_hip: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    attempts: list[str] = field(default_factory=list)

    @property
    def center(self) -> tuple[float, float] | None:
        if self.wcs is None:
            return None
        c = self.wcs.pixel_to_world((self.width - 1) / 2, (self.height - 1) / 2)
        return float(c.ra.deg), float(c.dec.deg)

    def fov_deg(self) -> tuple[float, float] | None:
        if self.wcs is None:
            return None
        w, h = self.width - 1, self.height - 1
        l, r = self.wcs.pixel_to_world([0, w], [h / 2, h / 2])
        t, b = self.wcs.pixel_to_world([w / 2, w / 2], [0, h])
        return float(l.separation(r).deg), float(t.separation(b).deg)

    def local_error(self, x: float, y: float, k: int = 8) -> float:
        """Predicted pixel error at (x, y): RMS of the k nearest matched stars' residuals,
        inflated when the point is far from any matched star (extrapolation)."""
        if len(self.match_resid) == 0:
            return 50.0
        tree = cKDTree(self.match_xy)
        kk = min(k, len(self.match_resid))
        d, i = tree.query([x, y], k=kk)
        d, i = np.atleast_1d(d), np.atleast_1d(i)
        local = float(np.sqrt(np.mean(self.match_resid[i] ** 2)))
        spacing = math.sqrt(self.width * self.height / max(len(self.match_resid), 1))
        return local * (1.0 + max(0.0, d[0] - spacing) / spacing)


def grade_for(matched: int, rms: float | None) -> str | None:
    if rms is None or matched < 6:
        return None
    if matched >= 100 and rms <= 1.0:
        return "A"
    if matched >= 30 and rms <= 2.0:
        return "B"
    return "C"


# ---------------------------------------------------------------- geometry helpers
def pixel_scale_deg(exif, width: int, height: int) -> float | None:
    """Full-resolution pixel scale (deg/px) at the image center from EXIF focal length."""
    f35 = exif.focal35_mm if exif and exif.focal35_mm else None
    if f35 is None and exif and exif.focal_mm:
        f35 = exif.focal_mm  # assume full frame
    if not f35:
        return None
    pitch = 36.0 / max(width, height)
    return math.degrees(math.atan(pitch / f35))


def span_deg(npx: float, scale_deg: float) -> float:
    """Angular span of npx pixels centered on the axis of a rectilinear lens."""
    pitch_over_f = math.tan(math.radians(scale_deg))
    return 2 * math.degrees(math.atan(npx / 2 * pitch_over_f))


def npx_for_span(deg: float, scale_deg: float) -> int:
    return int(2 * math.tan(math.radians(deg / 2)) / math.tan(math.radians(scale_deg)))


def _wcs_from_header_dict(h: dict, nx: int, ny: int) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [float(h["CRPIX1"]), float(h["CRPIX2"])]
    w.wcs.crval = [float(h["CRVAL1"]), float(h["CRVAL2"])]
    w.wcs.cd = [[float(h["CD1_1"]), float(h["CD1_2"])], [float(h["CD2_1"]), float(h["CD2_2"])]]
    w.pixel_shape = (nx, ny)
    return w


def wcs_to_full(sub_wcs: WCS, nx: int, ny: int, to_full) -> WCS:
    """Re-express a WCS defined on a crop/binned image in full-resolution image pixels.

    `to_full(u, v)` maps crop pixel coords (0-based) to full-res pixel coords (0-based)."""
    gu, gv = np.meshgrid(np.linspace(0, nx - 1, 9), np.linspace(0, ny - 1, 9))
    gu, gv = gu.ravel(), gv.ravel()
    sky = sub_wcs.pixel_to_world(gu, gv)
    fx, fy = to_full(gu, gv)
    # proj_point='center' mis-centres some fields (seen near RA 0/360 and high dec): pass it explicitly
    return fit_wcs_from_points((fx, fy), sky, proj_point=sky[len(sky) // 2], projection="TAN")


# ---------------------------------------------------------------- ASTAP
def run_astap(fits_path: Path, fov: float, exe: Path, db: Path | None, timeout: int = 180,
              downsample: int | None = None) -> dict | None:
    cmd = [str(exe), "-f", str(fits_path), "-fov", f"{fov:.3f}", "-r", "180"]
    if db:
        cmd += ["-d", str(db)]
        dbs = available_databases(db)
        pick = "w08" if fov >= 20 and "w08" in dbs else None
        if pick is None and fov < 20:
            pick = next((d for d in ("d50", "d20", "d05", "v50", "h18", "g05") if d in dbs), None)
        if pick:
            cmd += ["-D", pick]
    if downsample is not None:
        cmd += ["-z", str(downsample)]
    ini = fits_path.with_suffix(".ini")
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None
    if not ini.exists():
        return None
    h = {}
    for line in ini.read_text(errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            h[k.strip()] = v.strip()
    if h.get("PLTSOLVD", "F").upper() != "T":
        return None
    return h


def _write_fits(a: np.ndarray, path: Path) -> None:
    lo, hi = np.percentile(a, 0.5), np.percentile(a, 99.995)
    scaled = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1) * 65000
    fits.PrimaryHDU(scaled.astype(np.uint16)).writeto(path, overwrite=True)


def astap_attempts(gray: np.ndarray, scale_full: float | None, full_w: int, full_h: int):
    """Yield (label, crop array, fov_height_deg, to_full) attempts, cheapest/likeliest first."""
    rh, rw = gray.shape
    R = full_w / rw  # reduce factor actually applied

    def make(cx_f: float, cy_f: float, size_full: int, bin_full: float, label: str):
        size_r = int(min(size_full / R, rw, rh))
        x0 = int(round(min(max(cx_f / R - size_r / 2, 0), rw - size_r)))
        y0 = int(round(min(max(cy_f / R - size_r / 2, 0), rh - size_r)))
        crop = gray[y0 : y0 + size_r, x0 : x0 + size_r]
        n = max(64, int(round(size_r * R / bin_full)))
        img = np.asarray(Image.fromarray(crop).resize((n, n), Image.BOX), dtype=np.float32)
        k = size_r / n

        def to_full(uu, vv, x0=x0, y0=y0, k=k):
            return (x0 + (np.asarray(uu) + 0.5) * k) * R - 0.5, (y0 + (np.asarray(vv) + 0.5) * k) * R - 0.5

        fov = span_deg(size_r * R, scale_full) if scale_full else 0.0
        return label, img, fov, to_full

    cx, cy = full_w / 2, full_h / 2
    if scale_full is None:
        yield make(cx, cy, min(full_w, full_h), max(1, min(full_w, full_h) / 1400), "center-auto")
        return
    fov_h = span_deg(full_h, scale_full)
    base_bin = min(8, max(1, round(120 / (scale_full * 3600))))
    if fov_h <= 35:  # narrow field: solve the frame directly
        side = min(full_w, full_h)
        yield make(cx, cy, side, max(1, side / 1500), "full")
        yield make(cx, cy, side, max(1, side / 1000), "full-bin")
        return
    s30 = min(npx_for_span(30, scale_full), full_h)
    s40 = min(npx_for_span(40, scale_full), full_h)
    yield make(cx, cy, s30, base_bin, f"c30/b{base_bin}")
    yield make(cx, cy, s30, base_bin + 1, f"c30/b{base_bin + 1}")
    yield make(cx, cy, s40, base_bin + 1, f"c40/b{base_bin + 1}")
    for dx, dy, tag in [(0, -0.25, "up"), (0, 0.25, "down"), (-0.25, 0, "left"), (0.25, 0, "right")]:
        yield make(cx + dx * full_w, cy + dy * full_h, s30, base_bin + 1, f"c30-{tag}")
    yield make(cx, cy, s30, max(1, base_bin - 1), f"c30/b{max(1, base_bin - 1)}")


def solve_with_astap(gray, scale_full, full_w, full_h, attempts_log: list[str]) -> WCS | None:
    exe, db = find_astap()
    if exe is None:
        attempts_log.append("astap: not found")
        return None
    with tempfile.TemporaryDirectory(prefix="astro-id-") as td:
        for label, img, fov, to_full in astap_attempts(gray, scale_full, full_w, full_h):
            fp = Path(td) / "crop.fits"
            for ext in (".ini", ".wcs"):
                fp.with_suffix(ext).unlink(missing_ok=True)
            _write_fits(img, fp)
            t0 = time.time()
            h = run_astap(fp, fov, exe, db)
            attempts_log.append(f"astap {label} fov={fov:.1f}: {'ok' if h else 'fail'} ({time.time() - t0:.1f}s)")
            if h:
                sub = _wcs_from_header_dict(h, img.shape[1], img.shape[0])
                return wcs_to_full(sub, img.shape[1], img.shape[0], to_full)
    return None


# ---------------------------------------------------------------- astrometry.net fallback
NOVA = "https://nova.astrometry.net/api"


def solve_with_astrometry_net(gray, full_w, full_h, api_key: str, attempts_log: list[str],
                              scale_full: float | None = None, timeout_s: int = 600) -> list[WCS]:
    """Upload a downscaled JPG privately; return candidate full-res WCSs (both row conventions)."""
    import io
    import json

    import requests

    rh, rw = gray.shape
    width = min(2000, rw)
    k = rw / width
    img = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).resize((width, round(rh / k)), Image.BOX)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    try:
        r = requests.post(f"{NOVA}/login", data={"request-json": json.dumps({"apikey": api_key})}, timeout=60)
        session = r.json().get("session")
        if not session:
            attempts_log.append("astrometry.net: login failed")
            return []
        args = {"session": session, "publicly_visible": "n", "allow_commercial_use": "n",
                "allow_modifications": "n"}
        if scale_full:
            sc = scale_full * 3600 * full_w / width
            args.update({"scale_units": "arcsecperpix", "scale_type": "ev", "scale_est": sc, "scale_err": 20})
        r = requests.post(f"{NOVA}/upload", files={"file": ("frame.jpg", buf.getvalue(), "image/jpeg")},
                          data={"request-json": json.dumps(args)}, timeout=120)
        subid = r.json().get("subid")
        t0 = time.time()
        job = None
        while time.time() - t0 < timeout_s:
            time.sleep(5)
            s = requests.get(f"{NOVA}/submissions/{subid}", timeout=60).json()
            jobs = [j for j in (s.get("jobs") or []) if j]
            if jobs:
                job = jobs[0]
                st = requests.get(f"{NOVA}/jobs/{job}", timeout=60).json().get("status")
                if st == "success":
                    break
                if st == "failure":
                    attempts_log.append("astrometry.net: job failed")
                    return []
        else:
            attempts_log.append("astrometry.net: timeout")
            return []
        wr = requests.get(f"https://nova.astrometry.net/wcs_file/{job}", timeout=60)
        hdr = fits.Header.fromstring(wr.content.decode("ascii", errors="replace"))
    except Exception as exc:  # network, JSON, ...
        attempts_log.append(f"astrometry.net: {type(exc).__name__}")
        return []
    sub = WCS(hdr, naxis=2)
    sub = sub.celestial if sub.has_celestial else sub
    nx, ny = img.size
    attempts_log.append("astrometry.net: ok")
    sx, sy = full_w / nx, full_h / ny

    def top_down(uu, vv):
        return (np.asarray(uu) + 0.5) * sx - 0.5, (np.asarray(vv) + 0.5) * sy - 0.5

    def bottom_up(uu, vv):
        return (np.asarray(uu) + 0.5) * sx - 0.5, (ny - 1 - np.asarray(vv) + 0.5) * sy - 0.5

    return [wcs_to_full(sub, nx, ny, top_down), wcs_to_full(sub, nx, ny, bottom_up)]


# ---------------------------------------------------------------- refit
def safe_world2pix(w: WCS, ra, dec, width: int, height: int, max_distortion: float = 0.05):
    """World -> full-res pixel, NaN where the projection cannot be trusted.

    A SIP polynomial is only valid near the stars it was fitted on; far outside it can fold distant sky
    back into the frame. A point is rejected when it lies more than the frame's half-diagonal + 10 deg from
    the center, or when its SIP position differs from the plain TAN position by more than
    `max_distortion` of the frame size."""
    ra = np.atleast_1d(np.asarray(ra, float))
    dec = np.atleast_1d(np.asarray(dec, float))
    c = w.pixel_to_world((width - 1) / 2, (height - 1) / 2)
    corner = w.pixel_to_world(0, 0)
    reach = min(85.0, float(c.separation(corner).deg) + 10.0)
    ra0, dec0 = math.radians(c.ra.deg), math.radians(c.dec.deg)
    cosang = (np.sin(np.radians(dec)) * math.sin(dec0)
              + np.cos(np.radians(dec)) * math.cos(dec0) * np.cos(np.radians(ra) - ra0))
    ok = cosang > math.cos(math.radians(reach))
    x = np.full(ra.shape, np.nan)
    y = np.full(ra.shape, np.nan)
    if not ok.any():
        return x, y
    lx, ly = w.wcs_world2pix(ra[ok], dec[ok], 0)
    if w.sip is not None:
        sx, sy = w.all_world2pix(ra[ok], dec[ok], 0, quiet=True)
        bad = ~(np.hypot(sx - lx, sy - ly) <= max_distortion * max(width, height))
        sx[bad] = np.nan
        sy[bad] = np.nan
    else:
        sx, sy = lx, ly
    x[ok], y[ok] = sx, sy
    return x, y


def _catalog_in_view(w: WCS, width: int, height: int, mag_max: float, epoch: float, pad: float = 0.0):
    cat = data.hipparcos()
    ra, dec = cat.at_epoch(epoch)
    idx = np.nonzero(cat.vmag <= mag_max)[0]
    if len(idx) == 0:
        return idx, np.zeros(0), np.zeros(0)
    x, y = safe_world2pix(w, ra[idx], dec[idx], width, height)
    ok = np.isfinite(x) & np.isfinite(y) & (x > -pad) & (y > -pad) & (x < width - 1 + pad) & (y < height - 1 + pad)
    return idx[ok], x[ok], y[ok]


def radial_factor(x, y, width: int, height: int, growth: float) -> np.ndarray:
    """1 at the center, 1 + growth at the corners (4th power of the radius): wide-angle fits are least
    certain in the corners, so matching and clipping tolerate more there."""
    rho = np.hypot(np.asarray(x) - (width - 1) / 2, np.asarray(y) - (height - 1) / 2) / (math.hypot(width, height) / 2)
    return 1.0 + growth * rho ** 4


def match(w: WCS, det: Detections, width: int, height: int, mag_max: float, radius: float, epoch: float,
          max_det: int | None = None, region: float | None = None, growth: float = 0.0, by_peak: bool = False):
    """Nearest-neighbour match of projected catalog stars to detections (one-to-one)."""
    idx, cx, cy = _catalog_in_view(w, width, height, mag_max, epoch)
    if region is not None and len(idx):
        r = np.hypot(cx - (width - 1) / 2, cy - (height - 1) / 2)
        keep = r <= region
        idx, cx, cy = idx[keep], cx[keep], cy[keep]
    order = det.order_by_peak() if by_peak else det.order()
    if max_det:
        order = order[:max_det]
    if len(idx) == 0 or len(order) == 0:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    tree = cKDTree(np.c_[det.x[order], det.y[order]])
    rmax = radius * radial_factor(cx, cy, width, height, growth)
    d, j = tree.query(np.c_[cx, cy], distance_upper_bound=float(rmax.max()))
    ok = np.isfinite(d) & (d <= rmax)
    ci, dj, dd = np.nonzero(ok)[0], order[j[ok]], d[ok]
    # one-to-one: keep the closest catalog star per detection
    best: dict[int, int] = {}
    for n, (a, b) in enumerate(zip(ci, dj)):
        if b not in best or dd[n] < dd[best[b]]:
            best[b] = n
    keep = np.array(sorted(best.values()), dtype=int)
    return idx[ci[keep]], dj[keep], dd[keep]


def gnomonic(ra, dec, ra0: float, dec0: float):
    """Sky (deg) -> TAN-plane standard coordinates xi, eta (deg) about (ra0, dec0)."""
    a, d = np.radians(ra), np.radians(dec)
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return np.degrees(xi), np.degrees(eta)


def fit_tan_sip(x, y, ra, dec, width: int, height: int, crval: tuple[float, float], order: int | None) -> WCS:
    """Linear least-squares TAN(-SIP) fit with CRPIX fixed at the frame center.

    xi, eta = full polynomial in (u, v) = pixel - center, up to `order` (1 = plain TAN). The linear part is
    the CD matrix, higher terms become SIP A/B (A = CD^-1 [xi_hi, eta_hi]). CRVAL is re-centred twice."""
    x, y, ra, dec = (np.asarray(t, float) for t in (x, y, ra, dec))
    order = max(1, order or 1)
    cx, cy = (width - 1) / 2, (height - 1) / 2
    u, v = x - cx, y - cy
    sc = max(width, height) / 2  # normalise for conditioning
    un, vn = u / sc, v / sc
    terms = [(p, q) for n in range(0, order + 1) for p in range(n, -1, -1) for q in [n - p]]
    M = np.stack([un ** p * vn ** q for p, q in terms], axis=1)
    ra0, dec0 = crval
    for _ in range(3):
        xi, eta = gnomonic(ra, dec, ra0, dec0)
        cxi, *_ = np.linalg.lstsq(M, xi, rcond=None)
        ceta, *_ = np.linalg.lstsq(M, eta, rcond=None)
        # move the tangent point to the sky position of the frame center (constant term)
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [ra0, dec0]
        w.wcs.crpix = [1.0, 1.0]
        w.wcs.cd = np.eye(2)
        c = w.wcs_pix2world([[cxi[0], ceta[0]]], 0)[0]  # intermediate (deg) -> sky with identity CD
        # with identity CD, pixel (0-based) p maps to intermediate = p + 1 - crpix = p
        ra0, dec0 = float(c[0]), float(c[1])
        if abs(cxi[0]) < 1e-7 and abs(ceta[0]) < 1e-7:
            break
    lin = {t: k for k, t in enumerate(terms)}
    cd = np.array([[cxi[lin[(1, 0)]], cxi[lin[(0, 1)]]], [ceta[lin[(1, 0)]], ceta[lin[(0, 1)]]]]) / sc
    out = WCS(naxis=2)
    out.wcs.crval = [ra0, dec0]
    out.wcs.crpix = [cx + 1, cy + 1]
    out.wcs.cd = cd
    out.pixel_shape = (width, height)
    if order >= 2:
        from astropy.wcs import Sip

        A = np.zeros((order + 1, order + 1))
        B = np.zeros((order + 1, order + 1))
        cdinv = np.linalg.inv(cd)
        for (p, q), k in lin.items():
            if p + q < 2:
                continue
            hi = np.array([cxi[k], ceta[k]]) / sc ** (p + q)
            ab = cdinv @ hi
            A[p, q], B[p, q] = ab[0], ab[1]
        out.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        out.sip = Sip(A, B, None, None, out.wcs.crpix)
    else:
        out.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    out.wcs.set()
    return out


def _fit(det: Detections, cat_idx: np.ndarray, det_idx: np.ndarray, epoch: float, sip: int | None,
         center: SkyCoord, width: int, height: int) -> WCS:
    cat = data.hipparcos()
    ra, dec = cat.at_epoch(epoch)
    return fit_tan_sip(det.x[det_idx], det.y[det_idx], ra[cat_idx], dec[cat_idx], width, height,
                       (float(center.ra.deg), float(center.dec.deg)), sip)


def _residuals(w: WCS, det: Detections, cat_idx, det_idx, epoch: float) -> np.ndarray:
    cat = data.hipparcos()
    ra, dec = cat.at_epoch(epoch)
    x, y = w.all_world2pix(ra[cat_idx], dec[cat_idx], 0, quiet=True)
    return np.hypot(x - det.x[det_idx], y - det.y[det_idx])


def refit(w0: WCS, det: Detections, width: int, height: int, epoch: float, log_: list[str]):
    """Grow the match from the solved center outwards and fit TAN-SIP over the whole frame.

    Each round re-matches with a radius set from the previous fit (about 2.5x its median residual),
    fits, clips 2.5x-median outliers and refits. SIP degree tops out at 4 (5 is unstable on 80+ deg fields).
    """
    half = math.hypot(width, height) / 2
    scale = max(width, height) / 3000.0  # radii are tuned on a ~3000 px wide frame
    w = w0
    rounds = [  # (region fraction, mag limit, max detections, sip degree, corner growth)
        (1.01, 3.0, 80, None, 0.0),  # coarse: bright stars only, wide radius (rescues crude/offset solutions)
        (0.35, 6.0, 800, None, 0.0),
        (0.60, 6.5, 1500, 2, 0.0),
        (1.01, 7.0, 3000, 3, CORNER_GROWTH),
        (1.01, 7.0, 3000, 3, CORNER_GROWTH),
        (1.01, 7.5, 4000, 4, CORNER_GROWTH),
        (1.01, 7.5, 4000, 4, CORNER_GROWTH),
        (1.01, 7.5, 4000, 4, CORNER_GROWTH),
    ]
    radius = 0.04 * max(width, height)
    cap = radius
    prev_med = np.inf
    ci = dj = np.zeros(0, int)
    for frac, mag, nmax, sip, growth in rounds:
        c2, d2, _ = match(w, det, width, height, mag, radius, epoch, nmax, frac * half, growth)
        need = 6 if sip is None else 8 * (sip + 1) * (sip + 2) // 2
        if len(c2) < need:
            log_.append(f"refit r={frac:.2f} m<{mag}: {len(c2)} matches, keep previous")
            continue
        center = w.pixel_to_world((width - 1) / 2, (height - 1) / 2)
        try:
            w_new = _fit(det, c2, d2, epoch, sip, center, width, height)
            res = _residuals(w_new, det, c2, d2, epoch)
            rf = radial_factor(det.x[d2], det.y[d2], width, height, growth)
            clip = res < max(2.5 * np.median(res / rf), 0.3 * scale) * rf
            if need <= clip.sum() < len(res):
                c2, d2 = c2[clip], d2[clip]
                w_new = _fit(det, c2, d2, epoch, sip, center, width, height)
                res = _residuals(w_new, det, c2, d2, epoch)
        except Exception as exc:
            log_.append(f"refit failed: {exc}")
            continue
        med_new = float(np.median(res)) if len(res) else np.inf
        if not np.all(np.isfinite(res)) or (len(ci) and med_new > 1.5 * prev_med + 0.2 * scale):
            log_.append(f"refit r={frac:.2f} sip={sip}: median {med_new:.2f}px, worse - keep previous")
            continue
        prev_med = med_new
        w, ci, dj = w_new, c2, d2
        med = float(np.median(res))
        log_.append(f"refit r={frac:.2f} m<{mag} sip={sip}: {len(ci)} matches, median {med:.2f}px")
        radius = float(min(cap, max(2.5 * med, 0.8 * scale)))
        if sip is None and frac > 1:  # after the coarse round, never search wider than the normal start
            cap = 5.0 * scale
            radius = min(radius, max(cap, 2.5 * med))
    if len(ci) == 0:
        return None, ci, dj, np.zeros(0)
    w, ci, dj = _grow_into_corners(w, det, ci, dj, width, height, epoch, scale, log_)
    res = _residuals(w, det, ci, dj, epoch)
    return w, ci, dj, res


def _rho(x, y, width: int, height: int) -> np.ndarray:
    return np.hypot(np.asarray(x) - (width - 1) / 2, np.asarray(y) - (height - 1) / 2) / (math.hypot(width, height) / 2)


def _sector_matches(w: WCS, det: Detections, width: int, height: int, epoch: float, rho_in: float, rho_out: float,
                    scale: float, sectors: int = 8):
    """Match bright stars in the ring rho_in..rho_out. Each angular sector first finds its own offset by voting
    over all catalog-detection pair offsets (the extrapolated model can be tens of px off there), then matches
    nearest neighbours around that offset."""
    half = math.hypot(width, height) / 2
    idx, cx, cy = _catalog_in_view(w, width, height, RING_MAG, epoch, pad=0.0)
    if len(idx) == 0:
        return np.zeros(0, int), np.zeros(0, int)
    rho = _rho(cx, cy, width, height)
    ang = np.arctan2(cy - (height - 1) / 2, cx - (width - 1) / 2)
    order = det.order_by_peak()[:RING_MAX_DET * 4]
    dx_, dy_ = det.x[order], det.y[order]
    tree = cKDTree(np.c_[dx_, dy_])
    vmax = RING_RADIUS * scale * 3
    bin_ = max(2.0, 1.5 * scale)
    out_c, out_d = [], []
    for k in range(sectors):
        a0, a1 = -math.pi + 2 * math.pi * k / sectors, -math.pi + 2 * math.pi * (k + 1) / sectors
        sel = (rho > rho_in) & (rho <= rho_out) & (ang >= a0) & (ang < a1)
        if sel.sum() < 3:
            continue
        pts = np.c_[cx[sel], cy[sel]]
        pairs = tree.query_ball_point(pts, vmax)
        offs = [(dx_[j] - p[0], dy_[j] - p[1]) for p, js in zip(pts, pairs) for j in js]
        if len(offs) < 3:
            continue
        offs = np.array(offs)
        nb = int(2 * vmax / bin_) + 1
        hist, xe, ye = np.histogram2d(offs[:, 0], offs[:, 1], bins=nb, range=[[-vmax, vmax], [-vmax, vmax]])
        hist = hist + np.roll(hist, 1, 0) + np.roll(hist, 1, 1) + np.roll(np.roll(hist, 1, 0), 1, 1)  # 2x2 smoothing
        i, j = np.unravel_index(int(np.argmax(hist)), hist.shape)
        if hist[i, j] < 3 or hist[i, j] < 0.3 * sel.sum():
            continue
        sx, sy = xe[i], ye[j]  # lower edges of the 2x2 window's upper-right bin ~ window center
        d, jj = tree.query(pts + [sx, sy], distance_upper_bound=2.5 * bin_)
        ok = np.isfinite(d)
        out_c.extend(idx[sel][ok])
        out_d.extend(order[jj[ok]])
    if not out_c:
        return np.zeros(0, int), np.zeros(0, int)
    c, d = np.array(out_c), np.array(out_d)
    _, first = np.unique(d, return_index=True)
    return c[first], d[first]


def _grow_into_corners(w: WCS, det: Detections, ci, dj, width: int, height: int, epoch: float, scale: float,
                       log_: list[str]):
    """Wide-angle frames: the tight full-frame rounds only match where the model is already good, so the
    corners (strongest distortion) stay empty and are extrapolated. Extend ring by ring with bright stars,
    each sector re-centred by offset voting, refit, then re-run tight rounds that now reach the corners."""
    for _ in range(8):
        rho_m = _rho(det.x[dj], det.y[dj], width, height)
        cov = float(np.percentile(rho_m, 99))
        if cov >= 0.97:
            break
        outer = min(1.01, cov + 0.10)
        cr, dr = _sector_matches(w, det, width, height, epoch, cov, outer, scale)
        new = ~np.isin(dr, dj)
        cr, dr = cr[new], dr[new]
        if len(cr) < 3:
            log_.append(f"corners: ring {cov:.2f}-{outer:.2f} has {len(cr)} bright matches, stop")
            break
        c2, d2 = np.r_[ci, cr], np.r_[dj, dr]
        center = w.pixel_to_world((width - 1) / 2, (height - 1) / 2)
        deg = 5 if len(c2) >= 200 else 4 if len(c2) >= 120 else 3
        w2 = _fit(det, c2, d2, epoch, deg, center, width, height)
        res = _residuals(w2, det, c2, d2, epoch)
        is_ring = np.r_[np.zeros(len(ci), bool), np.ones(len(cr), bool)]
        thr = np.where(is_ring, max(3.0 * np.median(res[is_ring]), 1.0 * scale),
                       max(3.0 * np.median(res[~is_ring]), 0.3 * scale))
        keep = res < thr
        if keep[is_ring].sum() < 3:
            log_.append(f"corners: ring {cov:.2f}-{outer:.2f} did not fit, stop")
            break
        c2, d2 = c2[keep], d2[keep]
        w = _fit(det, c2, d2, epoch, deg, center, width, height)
        ci, dj = c2, d2
        log_.append(f"corners: ring {cov:.2f}-{outer:.2f} +{int(keep[is_ring].sum())} bright stars (sip {deg})")
    # tight full-frame rounds with the corner-aware model
    for _ in range(3):
        res = _residuals(w, det, ci, dj, epoch)
        radius = max(2.5 * float(np.median(res)), 0.8 * scale)
        c2, d2, _ = match(w, det, width, height, 7.5, radius, epoch, 4000, None)
        if len(c2) < 120:
            break
        center = w.pixel_to_world((width - 1) / 2, (height - 1) / 2)
        deg = 5 if len(c2) >= 300 else 4
        w2 = _fit(det, c2, d2, epoch, deg, center, width, height)
        r2 = _residuals(w2, det, c2, d2, epoch)
        keep = r2 < max(2.5 * np.median(r2), 0.3 * scale)
        c2, d2 = c2[keep], d2[keep]
        w2 = _fit(det, c2, d2, epoch, deg, center, width, height)
        w, ci, dj = w2, c2, d2
    rho_m = _rho(det.x[dj], det.y[dj], width, height)
    log_.append(f"final: {len(ci)} matches, coverage rho98 {np.percentile(rho_m, 98):.2f}")
    return w, ci, dj


def limiting_mag(w: WCS, det: Detections, width: int, height: int, epoch: float, radius: float,
                 sky_xy: np.ndarray | None = None) -> float | None:
    """Faintest 0.25-mag bin in which at least half the catalog stars have a detection within `radius`,
    after removing the chance-coincidence rate of the detection density. Only catalog stars near matched
    stars count (foreground, trees and cloud are not sky)."""
    idx, cx, cy = _catalog_in_view(w, width, height, 11.0, epoch)
    if len(idx) < 20 or len(det) == 0:
        return None
    if sky_xy is not None and len(sky_xy) >= 10:
        near = cKDTree(sky_xy).query(np.c_[cx, cy])[0]
        spacing = math.sqrt(width * height / len(sky_xy))
        keep = near <= 1.5 * spacing
        idx, cx, cy = idx[keep], cx[keep], cy[keep]
    tree = cKDTree(np.c_[det.x, det.y])
    d, _ = tree.query(np.c_[cx, cy], distance_upper_bound=radius)
    hit = np.isfinite(d)
    p0 = min(0.9, len(det) / (width * height) * math.pi * radius ** 2)
    mags = data.hipparcos().vmag[idx]
    lim = None
    for m in np.arange(3.0, 11.0, 0.25):
        sel = (mags >= m) & (mags < m + 0.25)
        if sel.sum() < 8:
            continue
        f = (hit[sel].mean() - p0) / (1 - p0)
        if f >= 0.5:
            lim = float(m + 0.25)
        elif lim is not None and m > lim + 0.5:
            break
    return lim


# ---------------------------------------------------------------- main entry
def solve_image(path: Path, exif, use_fallback: bool = True, epoch: float = 2026.0) -> Solution:
    from .imageio import load_gray

    attempts: list[str] = []
    gray = load_gray(path, REDUCE)
    full_w, full_h = exif.width, exif.height
    R = full_w / gray.shape[1]
    cell = max(32, gray.shape[1] // 75)
    # Several smoothing scales: sharp stars need ~1 px, defocused discs need several px.
    dets = {sm: detect(gray, sigma=5.0, smooth=sm, cell=max(cell, int(10 * sm))) for sm in SMOOTH_SCALES}
    det_r = dets[SMOOTH_SCALES[0]]
    scale = pixel_scale_deg(exif, full_w, full_h)
    sol = Solution(False, width=full_w, height=full_h, det=det_r.scaled(R), psf_px=fwhm_estimate(det_r) * R,
                   attempts=attempts)
    if max(len(d) for d in dets.values()) < 8:
        sol.error = f"too few stars ({len(det_r)} sources)"
        return sol

    candidates: list[tuple[str, WCS]] = []
    w0 = solve_with_astap(gray, scale, full_w, full_h, attempts)
    if w0 is not None:
        candidates.append(("astap", w0))
    else:
        key = os.environ.get("ASTROMETRY_API_KEY")
        if use_fallback and key:
            candidates += [("astrometry.net", c) for c in solve_with_astrometry_net(gray, full_w, full_h, key, attempts, scale)]
        elif use_fallback:
            attempts.append("astrometry.net: skipped (no ASTROMETRY_API_KEY)")
    if not candidates:
        sol.error = "no plate solution" + ("" if find_astap()[0] else " (ASTAP not installed)")
        return sol

    # Score each detection scale by its center agreement with the first solution, then fully
    # refit the competitive ones and keep the lowest RMS (defocused discs centroid better smoothed).
    half = math.hypot(full_w, full_h) / 2
    rad = max(5.0 * max(full_w, full_h) / 3000.0, 10.0)
    scores = {}
    for sm, d in dets.items():
        dd = d.scaled(R)
        scores[sm] = max(len(match(wc, dd, full_w, full_h, 6.0, rad, epoch, 800, 0.35 * half)[0]) for _, wc in candidates)
    attempts.append("detect scale " + ", ".join(f"s{k}={v}" for k, v in scores.items()))
    top = max(scores.values())
    best = None
    for sm in [k for k, v in scores.items() if v >= 0.75 * top and v >= 6]:
        det = dets[sm].scaled(R)
        for solver, wc in candidates:
            sub_log: list[str] = []
            w, ci, dj, res = refit(wc, det, full_w, full_h, epoch, sub_log)
            attempts.extend(f"[s{sm}] {m}" for m in sub_log)
            if w is None or len(ci) < 6:
                continue
            rms = float(np.sqrt(np.mean(res ** 2)))
            key = (len(ci) >= 20, -rms if len(ci) >= 20 else len(ci))
            if best is None or key > best[0]:
                best = (key, sm, solver, w, ci, dj, res, det)
    if best is not None:
        _, sm, solver, w, ci, dj, res, det = best
        attempts.append(f"chose detect scale s{sm}")
        sol.det, sol.psf_px = det, fwhm_estimate(dets[sm]) * R
        best = (solver, w, ci, dj, res)
    if best is None or len(best[2]) < 6:
        sol.error = "solution did not verify against catalog"
        return sol
    solver, w, ci, dj, res = best
    rms = float(np.sqrt(np.mean(res ** 2)))
    sol.solved, sol.solver, sol.wcs = True, solver, w
    sol.matched, sol.rms_px = len(ci), round(rms, 2)
    sol.grade = grade_for(len(ci), rms)
    sol.match_xy = np.c_[det.x[dj], det.y[dj]]
    sol.match_resid = res
    sol.match_hip = data.hipparcos().hip[ci]
    sol.lim_mag = limiting_mag(w, sol.det, full_w, full_h, epoch, max(2.0, 2.5 * rms), sol.match_xy)
    return sol
