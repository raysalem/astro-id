"""Reference data: downloaded on first use into a per-user cache, never vendored.

Sources and licenses are listed in README.md. Nothing here is shipped in the repo.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger("astro_id")

HIP_URLS = [
    "https://cdsarc.cds.unistra.fr/ftp/cats/I/239/hip_main.dat",
    "https://vizier.cfa.harvard.edu/ftp/cats/I/239/hip_main.dat",
]
OPENNGC_URLS = {
    "NGC.csv": "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv",
    "addendum.csv": "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/addendum.csv",
}
IAU_NAMES_URL = "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt"
CONSTELLATION_LINES_URL = (
    "https://raw.githubusercontent.com/Stellarium/stellarium/v23.4/skycultures/modern/constellationship.fab"
)

# The 88 IAU constellations (abbreviation -> name). Facts, not a copyrighted dataset.
CONSTELLATION_NAMES = {
    "And": "Andromeda", "Ant": "Antlia", "Aps": "Apus", "Aqr": "Aquarius", "Aql": "Aquila", "Ara": "Ara",
    "Ari": "Aries", "Aur": "Auriga", "Boo": "Bootes", "Cae": "Caelum", "Cam": "Camelopardalis",
    "Cnc": "Cancer", "CVn": "Canes Venatici", "CMa": "Canis Major", "CMi": "Canis Minor",
    "Cap": "Capricornus", "Car": "Carina", "Cas": "Cassiopeia", "Cen": "Centaurus", "Cep": "Cepheus",
    "Cet": "Cetus", "Cha": "Chamaeleon", "Cir": "Circinus", "Col": "Columba", "Com": "Coma Berenices",
    "CrA": "Corona Australis", "CrB": "Corona Borealis", "Crv": "Corvus", "Crt": "Crater", "Cru": "Crux",
    "Cyg": "Cygnus", "Del": "Delphinus", "Dor": "Dorado", "Dra": "Draco", "Equ": "Equuleus",
    "Eri": "Eridanus", "For": "Fornax", "Gem": "Gemini", "Gru": "Grus", "Her": "Hercules",
    "Hor": "Horologium", "Hya": "Hydra", "Hyi": "Hydrus", "Ind": "Indus", "Lac": "Lacerta", "Leo": "Leo",
    "LMi": "Leo Minor", "Lep": "Lepus", "Lib": "Libra", "Lup": "Lupus", "Lyn": "Lynx", "Lyr": "Lyra",
    "Men": "Mensa", "Mic": "Microscopium", "Mon": "Monoceros", "Mus": "Musca", "Nor": "Norma",
    "Oct": "Octans", "Oph": "Ophiuchus", "Ori": "Orion", "Pav": "Pavo", "Peg": "Pegasus", "Per": "Perseus",
    "Phe": "Phoenix", "Pic": "Pictor", "Psc": "Pisces", "PsA": "Piscis Austrinus", "Pup": "Puppis",
    "Pyx": "Pyxis", "Ret": "Reticulum", "Sge": "Sagitta", "Sgr": "Sagittarius", "Sco": "Scorpius",
    "Scl": "Sculptor", "Sct": "Scutum", "Ser": "Serpens", "Sex": "Sextans", "Tau": "Taurus",
    "Tel": "Telescopium", "Tri": "Triangulum", "TrA": "Triangulum Australe", "Tuc": "Tucana",
    "UMa": "Ursa Major", "UMi": "Ursa Minor", "Vel": "Vela", "Vir": "Virgo", "Vol": "Volans",
    "Vul": "Vulpecula",
}


def cache_dir() -> Path:
    env = os.environ.get("ASTRO_ID_DATA")
    if env:
        p = Path(env)
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
        p = Path(base) / "astro-id" / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download(urls: list[str] | str, dest: Path, timeout: int = 600) -> Path:
    import requests

    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if isinstance(urls, str):
        urls = [urls]
    last = None
    for url in urls:
        try:
            log.info("downloading %s", url)
            r = requests.get(url, timeout=(20, timeout), stream=True)
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as exc:  # try next mirror
            last = exc
            log.warning("download failed from %s: %s", url, exc)
    raise RuntimeError(f"could not download {dest.name}: {last}")


# ---------------------------------------------------------------- Hipparcos
@dataclass
class StarCatalog:
    hip: np.ndarray
    ra: np.ndarray  # deg, J2000/ICRS, epoch J1991.25
    dec: np.ndarray
    vmag: np.ndarray
    pmra: np.ndarray  # mas/yr (includes cos dec)
    pmdec: np.ndarray

    def at_epoch(self, year: float) -> tuple[np.ndarray, np.ndarray]:
        dt = year - 1991.25
        dec = self.dec + self.pmdec * dt / 3.6e6
        cosd = np.cos(np.radians(self.dec))
        ra = self.ra + np.where(cosd > 1e-6, self.pmra * dt / 3.6e6 / np.maximum(cosd, 1e-6), 0.0)
        return ra % 360.0, dec

    def index_of(self) -> dict[int, int]:
        return {int(h): i for i, h in enumerate(self.hip)}


def _parse_hip_main(path: Path) -> StarCatalog:
    hip, ra, dec, vm, pmra, pmdec = [], [], [], [], [], []
    with open(path, "r", encoding="ascii", errors="replace") as fh:
        for line in fh:
            f = line.split("|")
            if len(f) < 14:
                continue
            try:
                v = float(f[5])
            except ValueError:
                continue
            try:
                r, d = float(f[8]), float(f[9])
            except ValueError:
                try:
                    h, m, s = (float(x) for x in f[3].split())
                    sign = -1 if f[4].strip().startswith("-") else 1
                    dd, dm, ds = (abs(float(x)) for x in f[4].split())
                    r = 15 * (h + m / 60 + s / 3600)
                    d = sign * (dd + dm / 60 + ds / 3600)
                except ValueError:
                    continue
            try:
                pa, pd = float(f[12]), float(f[13])
            except ValueError:
                pa = pd = 0.0
            hip.append(int(f[1]))
            ra.append(r)
            dec.append(d)
            vm.append(v)
            pmra.append(pa)
            pmdec.append(pd)
    return StarCatalog(np.array(hip), np.array(ra), np.array(dec), np.array(vm), np.array(pmra), np.array(pmdec))


@lru_cache(maxsize=1)
def hipparcos() -> StarCatalog:
    d = cache_dir()
    npz = d / "hipparcos.npz"
    if npz.exists():
        z = np.load(npz)
        return StarCatalog(z["hip"], z["ra"], z["dec"], z["vmag"], z["pmra"], z["pmdec"])
    raw = _download(HIP_URLS, d / "hip_main.dat")
    cat = _parse_hip_main(raw)
    np.savez_compressed(npz, hip=cat.hip, ra=cat.ra, dec=cat.dec, vmag=cat.vmag, pmra=cat.pmra, pmdec=cat.pmdec)
    return cat


# ---------------------------------------------------------------- ephemeris
@lru_cache(maxsize=1)
def ephemeris():
    from skyfield.api import Loader

    load = Loader(str(cache_dir()), verbose=False)
    return load.timescale(builtin=True), load("de421.bsp")


# ---------------------------------------------------------------- IAU names
def parse_iau_csn(text: str) -> dict[int, tuple[str, float]]:
    """IAU-CSN.txt (WGSN) -> {HIP: (name, Vmag)}. Only entries with a HIP number."""
    out: dict[int, tuple[str, float]] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith(("#", "$")):
            continue
        name = line[:18].strip()
        toks = line[18:].split()
        if toks and toks[-1] == "*":
            toks = toks[:-1]
        # tail: ... Vmag band HIP HD RA Dec date
        if len(toks) < 7 or not re.match(r"\d{4}-\d{2}-\d{2}", toks[-1]):
            continue
        hip_s, vmag_s = toks[-5], toks[-7]
        if not hip_s.isdigit():
            continue
        try:
            vmag = float(vmag_s)
        except ValueError:
            vmag = 99.0
        out[int(hip_s)] = (name, vmag)
    return out


@lru_cache(maxsize=1)
def star_names() -> dict[int, tuple[str, float]]:
    p = _download(IAU_NAMES_URL, cache_dir() / "IAU-CSN.txt")
    return parse_iau_csn(p.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- constellation lines
def parse_constellationship(text: str) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for line in text.splitlines():
        toks = line.split()
        if len(toks) < 2 or line.startswith("#"):
            continue
        abbr, n = toks[0], int(toks[1])
        ids = [int(t) for t in toks[2 : 2 + 2 * n]]
        out.setdefault(abbr, []).extend(zip(ids[0::2], ids[1::2]))
    return out


@lru_cache(maxsize=1)
def constellation_lines() -> dict[str, list[tuple[int, int]]]:
    p = _download(CONSTELLATION_LINES_URL, cache_dir() / "constellationship.fab")
    return parse_constellationship(p.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- deep-sky objects (OpenNGC)
@dataclass
class DSO:
    name: str
    kind: str
    ra: float
    dec: float
    vmag: float
    size_arcmin: float
    surf_br: float | None
    messier: int | None


_KIND = {
    "G": "galaxy", "GPair": "galaxy", "GTrpl": "galaxy", "GGroup": "galaxy",
    "OCl": "cluster", "GCl": "cluster", "*Ass": "cluster", "Cl+N": "cluster",
    "PN": "nebula", "HII": "nebula", "EmN": "nebula", "Neb": "nebula", "RfN": "nebula", "SNR": "nebula",
    "DrkN": "nebula",
}


def _hms(s: str) -> float:
    h, m, sec = (float(x) for x in s.split(":"))
    return 15 * (h + m / 60 + sec / 3600)


def _dms(s: str) -> float:
    sign = -1 if s.strip().startswith("-") else 1
    d, m, sec = (abs(float(x)) for x in s.split(":"))
    return sign * (d + m / 60 + sec / 3600)


def _f(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# Visual magnitudes for Messier objects that OpenNGC lists without one (published values, e.g. SEDS).
MESSIER_VMAG_FILL = {35: 5.3, 107: 7.9, 62: 6.5, 69: 7.6, 22: 5.1, 70: 7.9, 71: 6.1}


def parse_openngc(text: str, mag_limit: float = 8.0) -> list[DSO]:
    """Messier objects plus NGC/IC (and addendum) objects brighter than mag_limit."""
    out: list[DSO] = []
    rdr = csv.DictReader(io.StringIO(text), delimiter=";")
    for row in rdr:
        typ = row.get("Type", "")
        if typ in ("Dup", "NonEx", "Other", "*", "**", "") or not row.get("RA"):
            continue
        m = row.get("M") or ""
        messier = int(m) if m.strip().isdigit() else None
        v = _f(row.get("V-Mag"))
        b = _f(row.get("B-Mag"))
        mag = v if v is not None else (b - 0.7 if b is not None else MESSIER_VMAG_FILL.get(messier))
        if messier is None and (mag is None or mag > mag_limit):
            continue
        try:
            ra, dec = _hms(row["RA"]), _dms(row["Dec"])
        except (ValueError, KeyError):
            continue
        raw = row["Name"]
        mm = re.match(r"(NGC|IC)0*(\d+)(.*)", raw)
        cat_name = f"{mm.group(1)} {mm.group(2)}{mm.group(3)}" if mm else raw
        common = (row.get("Common names") or "").split(",")[0].strip()
        if messier is not None:
            label = f"M{messier}" + (f" {common}" if common else "")
        else:
            label = cat_name + (f" {common}" if common else "")
        size = _f(row.get("MajAx")) or 1.0
        out.append(
            DSO(label, _KIND.get(typ, "other"), ra, dec, mag if mag is not None else 9.0, size,
                _f(row.get("SurfBr")), messier)
        )
    return out


@lru_cache(maxsize=1)
def deep_sky() -> list[DSO]:
    d = cache_dir()
    objs: list[DSO] = []
    seen_m: set[int] = set()
    for fname, url in OPENNGC_URLS.items():
        p = _download(url, d / fname)
        for o in parse_openngc(p.read_text(encoding="utf-8", errors="replace")):
            if o.messier is not None:
                if o.messier in seen_m:
                    continue
                seen_m.add(o.messier)
            objs.append(o)
    return objs


def ensure_all() -> dict[str, str]:
    """Download everything up front; returns {dataset: path or error}."""
    status = {}
    for name, fn in [("hipparcos", hipparcos), ("de421", ephemeris), ("names", star_names),
                     ("lines", constellation_lines), ("openngc", deep_sky)]:
        try:
            fn()
            status[name] = "ok"
        except Exception as exc:
            status[name] = f"error: {exc}"
    return status


def dump_status() -> str:
    return json.dumps(ensure_all(), indent=1)
