"""Star / bright-source detection on a grayscale array."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi


@dataclass
class Detections:
    x: np.ndarray  # pixel coords in the array that was searched (0-based, origin top-left)
    y: np.ndarray
    flux: np.ndarray
    peak: np.ndarray
    npix: np.ndarray
    noise: float
    background: float

    def __len__(self) -> int:
        return len(self.x)

    def scaled(self, factor: float) -> "Detections":
        """Map coordinates from a reduced array back to full resolution."""
        return Detections((self.x + 0.5) * factor - 0.5, (self.y + 0.5) * factor - 0.5, self.flux,
                          self.peak, self.npix * factor * factor, self.noise, self.background)

    def order(self) -> np.ndarray:
        """Brightest first: flux within the blob."""
        return np.argsort(-self.flux)

    def order_by_peak(self) -> np.ndarray:
        """Brightest first by peak (saturated stars and planets on top), flux breaking ties. Large diffuse
        blobs (Milky Way patches) have big summed flux but modest peaks, so they sink."""
        return np.lexsort((-self.flux, -self.peak))


def background_map(img: np.ndarray, cell: int = 64) -> np.ndarray:
    h, w = img.shape
    gh, gw = max(1, h // cell), max(1, w // cell)
    small = img[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell)
    med = np.median(small, axis=(1, 3))
    med = ndi.median_filter(med, size=3, mode="nearest")
    zoom = (h / gh, w / gw)
    bg = ndi.zoom(med, zoom, order=1, mode="nearest")
    out = np.empty_like(img)
    hh, ww = min(h, bg.shape[0]), min(w, bg.shape[1])
    out[:hh, :ww] = bg[:hh, :ww]
    if hh < h:
        out[hh:, :] = out[hh - 1 : hh, :]
    if ww < w:
        out[:, ww:] = out[:, ww - 1 : ww]
    return out


def detect(img: np.ndarray, sigma: float = 5.0, smooth: float = 1.0, max_npix: int | None = None,
           cell: int = 64) -> Detections:
    """Threshold above a smooth background, label blobs, return flux-weighted centroids."""
    bg = background_map(img, cell)
    res = img - bg
    mad = np.median(np.abs(res - np.median(res)))
    noise = float(max(1.4826 * mad, 1e-3))
    sm = ndi.gaussian_filter(res, smooth) if smooth > 0 else res
    noise_s = float(max(1.4826 * np.median(np.abs(sm - np.median(sm))), 1e-4))
    mask = sm > sigma * noise_s
    lab, n = ndi.label(mask)
    if n == 0:
        z = np.zeros(0)
        return Detections(z, z, z, z, z, noise, float(np.median(bg)))
    idx = np.arange(1, n + 1)
    w = np.clip(res, 0, None)
    flux = ndi.sum(w, lab, idx)
    npix = ndi.sum(np.ones_like(res), lab, idx)
    peak = ndi.maximum(img, lab, idx)
    with np.errstate(invalid="ignore", divide="ignore"):
        cy, cx = np.array(ndi.center_of_mass(w, lab, idx)).T
    keep = (npix >= max(2, smooth * smooth)) & np.isfinite(cx) & (flux > 0)
    if max_npix:
        keep &= npix <= max_npix
    return Detections(cx[keep], cy[keep], flux[keep], peak[keep], npix[keep], noise, float(np.median(bg)))


def fwhm_estimate(det: Detections) -> float:
    """Rough PSF FWHM (pixels of the searched array) from blob areas of mid-bright stars."""
    if len(det) < 5:
        return 3.0
    o = det.order()
    mid = o[len(o) // 10 : max(len(o) // 2, len(o) // 10 + 1)]
    area = np.median(det.npix[mid])
    return float(max(1.5, 2 * np.sqrt(area / np.pi)))
