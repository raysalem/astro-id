"""Build docs/img/demo.mp4 (about 40 s) and demo.gif (about 10 s) from real astro-id runs on the example photos.

Run from the repo root (needs ASTAP + W08, like `astro-id solve`; about a minute):
    uv run --with imageio-ffmpeg python tools/make_demo.py            # solve, render, encode
    uv run --with imageio-ffmpeg python tools/make_demo.py --reuse    # reuse build/demo/*/out from the last run
(ffmpeg on PATH is used if imageio-ffmpeg is not installed.)

Every label in the video is real output: frames 04 (Orion rising) and 05 (dawn, Jupiter and Mars) are solved four
times with `astro-id solve` into build/demo/<run>/, each run with its own astro-id.json:
    full      site + clock offset (examples/astro-id.json)
    nosite    clock offset only
    nooffset  site only (camera clock taken as is)
    neither   neither (timezone only)
The pictures are the solver's labeled copy (scene 2) and `astro_id.export.render` of each run's annotations payload
for a Sky-labels view (groups, "Only names at confidence >=", show scores), exactly what Facet's Export burns. The
cards beside the picture mirror the Facet panel's controls and show the setting each picture was rendered with;
every number in a caption or card is read from the runs. The script stops if a run no longer shows what a caption
says (e.g. Jupiter missing from the full run).
"""
import argparse, json, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from astro_id.config import Site  # noqa: E402
from astro_id.export import render, settings, visible_items  # noqa: E402
from astro_id.identify import altitudes  # noqa: E402

EX, OUT, WORK = ROOT / "examples", ROOT / "docs" / "img", ROOT / "build" / "demo"
W, H, FPS = 1280, 720, 24
SRC_W = 3200                  # working width for full-frame zooms (scene 2)
LEGEND = (0.215, 0.86)        # the solver's legend box: x < 21.5 % of the width, y > 86 % of the height
PW, PH = 900, 600             # picture pane (3:2) of the control scenes; card column to its right, caption below
F4, F5 = "04-orion-rising.jpg", "05-dawn-jupiter-mars.jpg"
CFG = json.loads((EX / "astro-id.json").read_text(encoding="utf-8"))
SITE = CFG["site"]
RUNS = {
    "full": CFG,
    "nosite": {k: v for k, v in CFG.items() if k != "site"},
    "nooffset": {k: v for k, v in CFG.items() if k != "clock_offset"},
    "neither": {k: v for k, v in CFG.items() if k not in ("site", "clock_offset")},
}
GROUPS = [("constellation", "Constellations"), ("planet", "Planets & Moon"), ("catalog", "Messier / NGC / IC"),
          ("asterism", "Asterisms"), ("star", "Stars")]
DEFAULT_GROUPS = {"constellation": True, "planet": True, "catalog": True, "asterism": True, "star": False}

# ---------------------------------------------------------------- runs


def solve_runs(reuse):
    res = {}
    for k, cfg in RUNS.items():
        d = WORK / k
        rf = d / "out" / "astro-id-results.json"
        if not (reuse and rf.exists()):
            shutil.rmtree(d, ignore_errors=True)
            (d / "in").mkdir(parents=True)
            (d / "in" / "astro-id.json").write_text(json.dumps(cfg), encoding="utf-8")
            for f in (F4, F5):
                shutil.copy2(EX / f, d / "in" / f)
            print("solving", k, "...", flush=True)
            subprocess.run([sys.executable, "-m", "astro_id.cli", "solve", str(d / "in"), "--no-fallback",
                            "--no-quality", "--stack-mode", "all", "--overlay-dir", str(d / "out")],
                           cwd=ROOT, check=True)
        doc = json.loads(rf.read_text(encoding="utf-8"))
        res[k] = {Path(r["file"]).name: r for r in doc["results"]}
        for f, r in res[k].items():
            assert r["solved"], (k, f)
    return res


def conf(fr, name):
    return next((o["confidence"] for o in fr["objects"] if o["name"] == name), None)


def view(groups=None, score=65, show_scores=False):
    return {"groups": dict(groups or DEFAULT_GROUPS), "score": score, "show_scores": show_scores}


def shown(fr, v):
    return len(visible_items(fr["annotations"], settings(fr["annotations"], v)))


def rend(fr, v, width, tag):
    src = WORK / "full" / "in" / Path(fr["file"]).name
    out = WORK / "renders" / f"{tag}.jpg"
    render(src, out, fr["annotations"], v, lines=True, scores=bool(v.get("show_scores")), legend=False, width=width)
    return Image.open(out).convert("RGB")


def hm(iso):
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")

# ---------------------------------------------------------------- drawing


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        p = shutil.which("ffmpeg")
        if not p:
            sys.exit("need ffmpeg: uv run --with imageio-ffmpeg python tools/make_demo.py")
        return p


_fonts = {}


def font(size, bold=False, mono=False):
    key = (size, bold, mono)
    if key not in _fonts:
        names = (["consolab.ttf", "consola.ttf", "DejaVuSansMono.ttf"] if mono else
                 ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else
                 ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
        for n in names:
            try:
                _fonts[key] = ImageFont.truetype(n, size)
                break
            except OSError:
                pass
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


BG, CARD, EDGE = (6, 9, 18), (22, 27, 40), (58, 68, 92)
WHITE, MUTED, ACCENT, HI, GOOD, WARN = (255, 255, 255), (165, 178, 205), (110, 175, 255), (255, 214, 102), \
    (130, 225, 150), (255, 150, 120)


def load_full(path, under=None):
    im = Image.open(path).convert("RGB")
    if under is not None:  # cover the solver's legend box (bottom-left) with the same pixels of the original
        o = Image.open(under).convert("RGB").resize(im.size)
        box = (0, round(im.height * LEGEND[1]), round(im.width * LEGEND[0]), im.height)
        im.paste(o.crop(box), box[:2])
    return im.resize((SRC_W, round(im.height * SRC_W / im.width)), Image.LANCZOS)


def fview(im, cx, cy, zoom):
    """Normalised view (cx, cy, zoom) of a full-width image -> WxH frame."""
    cw = SRC_W / zoom
    ch = cw * H / W
    x0 = min(max(cx * im.width - cw / 2, 0), im.width - cw)
    y0 = min(max(cy * im.height - ch / 2, 0), im.height - ch)
    return im.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))


def bar(img, text, sub=None, top=H - 112):
    img = img.convert("RGBA")
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle((0, top, W, H), fill=(5, 8, 16, 205))
    d.text((32, top + 12), text, font=font(34, True), fill=WHITE)
    if sub:
        d.text((32, top + 62), sub, font=font(25), fill=GOOD)
    return Image.alpha_composite(img, ov).convert("RGB")


def title_card(bg, lines, alpha=1.0):
    dark = Image.new("RGB", (W, H), BG)
    base = Image.blend(dark, bg.filter(ImageFilter.GaussianBlur(6)), 0.25)
    d = ImageDraw.Draw(base)
    y = (H - sum(f.size + 24 for _, f, _ in lines)) // 2
    for t, f, col in lines:
        d.text(((W - d.textlength(t, font=f)) / 2, y), t, font=f, fill=col)
        y += f.size + 24
    return Image.blend(dark, base, alpha) if alpha < 1 else base


def wrap(d, text, f, width):
    out, line = [], ""
    for w in text.split():
        t = (line + " " + w).strip()
        if d.textlength(t, font=f) <= width or not line:
            line = t
        else:
            out.append(line)
            line = w
    return out + ([line] if line else [])


def panel(title, rows):
    """A Facet-panel-like control card (380 x 600). rows: ("field", label, value, state) state in
    {"empty", "set", "changed"}; ("slider", label, value); ("check", label, on, changed); ("switch", label, on);
    ("note", text[, colour]); ("big", text, colour); ("gap", px)."""
    im = Image.new("RGB", (W - PW, PH), BG)
    d = ImageDraw.Draw(im)
    x0, x1 = 14, W - PW - 14
    d.rounded_rectangle((x0, 14, x1, PH - 14), 14, fill=CARD, outline=EDGE, width=2)
    d.text((x0 + 18, 26), title, font=font(27, True), fill=WHITE)
    y, x, w = 74, x0 + 18, x1 - x0 - 36
    for r in rows:
        kind = r[0]
        if kind == "gap":
            y += r[1]
        elif kind == "field":
            _, lab, val, state = r
            d.text((x, y), lab, font=font(20), fill=MUTED)
            y += 28
            col = {"empty": (40, 46, 62), "set": (34, 44, 66), "changed": (52, 46, 22)}[state]
            edge = HI if state == "changed" else EDGE
            lines = wrap(d, val, font(23, True), w - 20)
            hh = 12 + 30 * len(lines)
            d.rounded_rectangle((x, y, x + w, y + hh), 8, fill=col, outline=edge, width=2)
            for i, ln in enumerate(lines):
                d.text((x + 10, y + 5 + 30 * i), ln, font=font(23, True), fill=MUTED if state == "empty" else WHITE)
            y += hh + 14
        elif kind == "slider":
            _, lab, val = r
            d.text((x, y), lab, font=font(21), fill=WHITE)
            y += 34
            tx0, tx1 = x + 6, x + w - 70
            ty = y + 12
            kx = tx0 + (tx1 - tx0) * val / 100
            d.rounded_rectangle((tx0, ty - 4, tx1, ty + 4), 4, fill=(60, 70, 95))
            d.rounded_rectangle((tx0, ty - 4, kx, ty + 4), 4, fill=ACCENT)
            d.ellipse((kx - 12, ty - 12, kx + 12, ty + 12), fill=WHITE, outline=ACCENT, width=3)
            d.text((tx1 + 16, y - 6), str(val), font=font(32, True), fill=HI)
            y += 44
        elif kind == "check":
            _, lab, on, changed = r
            d.rounded_rectangle((x, y + 3, x + 24, y + 27), 5, fill=ACCENT if on else (40, 46, 62),
                                outline=HI if changed else (120, 135, 165), width=2)
            if on:
                d.line([(x + 5, y + 15), (x + 10, y + 21), (x + 19, y + 8)], fill=WHITE, width=3)
            d.text((x + 36, y), lab, font=font(23, changed), fill=HI if changed else (WHITE if on else MUTED))
            y += 38
        elif kind == "switch":
            _, lab, on = r
            d.rounded_rectangle((x, y + 4, x + 48, y + 28), 12, fill=ACCENT if on else (70, 78, 98))
            kx = x + 36 if on else x + 12
            d.ellipse((kx - 9, y + 7, kx + 9, y + 25), fill=WHITE)
            d.text((x + 58, y + 1), lab, font=font(19, True), fill=WHITE)
            y += 40
        elif kind == "note":
            col = r[2] if len(r) > 2 else MUTED
            for ln in wrap(d, r[1], font(20), w):
                d.text((x, y), ln, font=font(20), fill=col)
                y += 27
            y += 6
        elif kind == "big":
            for ln in r[1].split("\n"):
                d.text((x, y), ln, font=font(38, True), fill=r[2])
                y += 46
    return im


def layout(pic, card, text, sub=None, tag=None):
    """Picture pane (900 x 600) + control card + caption bar."""
    im = Image.new("RGB", (W, H), BG)
    im.paste(pic.resize((PW, PH), Image.LANCZOS) if pic.size != (PW, PH) else pic, (0, 0))
    im.paste(card, (PW, 0))
    if tag:
        d = ImageDraw.Draw(im, "RGBA")
        tw = d.textlength(tag, font=font(19))
        d.rounded_rectangle((10, 10, 30 + tw, 42), 6, fill=(0, 0, 0, 170))
        d.text((20, 13), tag, font=font(19), fill=MUTED)
    return bar(im, text, sub, top=PH)


def crop_1to1(im, cx, cy):
    x0 = int(min(max(cx - PW / 2, 0), im.width - PW))
    y0 = int(min(max(cy - PH / 2, 0), im.height - PH))
    return im.crop((x0, y0, x0 + PW, y0 + PH))


def ease(t):
    t = min(max(t, 0), 1)
    return t * t * (3 - 2 * t)


def n(s):
    return int(round(s * FPS))


def hold(img, s):
    for _ in range(n(s)):
        yield img


def xfade(a, b, s):
    for i in range(n(s)):
        yield Image.blend(a, b, ease((i + 1) / n(s)))

# ---------------------------------------------------------------- scenes


def build(R):
    full4, full5 = R["full"][F4], R["full"][F5]
    ns5, no5, ne4, ne5 = R["nosite"][F5], R["nooffset"][F5], R["neither"][F4], R["neither"][F5]
    J = {k: conf(R[k][F5], "Jupiter") for k in RUNS}
    M = {k: conf(R[k][F5], "Mars") for k in RUNS}
    # what the captions say must still be true
    assert J["full"] >= 80 and M["full"] >= 80, J
    assert J["nosite"] is not None and J["nosite"] < 80 and M["nosite"] is not None and M["nosite"] < 80, (J, M)
    assert not no5["objects"], "site + raw clock: expected nothing named on the dawn frame"
    assert J["neither"] is None and M["neither"] is None, "wrong date: expected Jupiter/Mars out of the frame"
    s = Site(SITE["lat"], SITE["lon"], SITE.get("elev_m", 0.0))
    raw_alt = float(altitudes([no5["center_ra_deg"]], [no5["center_dec_deg"]],
                              [datetime.fromisoformat(no5["true_time"])], s)[0][0])
    assert raw_alt < 0, raw_alt
    a4 = {o["name"]: o["confidence"] for o in full4["objects"]}
    b4 = {o["name"]: o["confidence"] for o in ne4["objects"]}
    same4 = sum(1 for k in a4 if b4.get(k) == a4[k])
    moved4 = sorted(k for k in set(a4) | set(b4) if a4.get(k) != b4.get(k))
    assert moved4 == ["Uranus"], moved4
    ura = (a4["Uranus"], b4["Uranus"])
    tot4 = len(full4["objects"])

    # pictures (all real renders)
    lab4 = load_full(WORK / "full" / "out" / (Path(F4).stem + "_astro.jpg"), under=EX / F4)
    orig4 = load_full(EX / F4)
    pj = [o for o in full5["objects"] if o["name"] in ("Jupiter", "Mars")]
    pcx, pcy = sum(o["x"] for o in pj) / 2, sum(o["y"] for o in pj) / 2
    sc5 = view(show_scores=True)
    d_nosite = crop_1to1(rend(ns5, sc5, 2400, "05-nosite"), pcx, pcy)
    d_full = crop_1to1(rend(full5, sc5, 2400, "05-full"), pcx, pcy)
    d_raw = crop_1to1(rend(no5, sc5, 2400, "05-nooffset"), pcx, pcy)
    o_neither = rend(ne4, view(), PW, "04-neither")
    conf_steps = [(c, rend(full4, view(score=c), PW, f"04-c{c}"), shown(full4, view(score=c))) for c in (65, 80, 95)]
    g_steps = []
    for i, on in enumerate([{"constellation"}, {"constellation", "catalog"},
                            {"constellation", "catalog", "planet", "asterism"},
                            {"constellation", "catalog", "planet", "asterism", "star"}]):
        g = {k: k in on for k, _ in GROUPS}
        g_steps.append((g, rend(full4, view(groups=g), PW, f"04-g{i}"), shown(full4, view(groups=g)), False))
    g_steps.append((DEFAULT_GROUPS, rend(full4, view(show_scores=True), PW, "04-gs"),
                    shown(full4, view(show_scores=True)), True))

    site_txt = f"{SITE['name']} · {SITE['lat']:.2f}, {SITE['lon']:.2f}"
    offset_txt = f"{CFG['clock_offset']} ± {CFG['time_tolerance_min']} min"
    numbers = {
        "objects_04": tot4, "stars_matched_04": full4["stars"],
        "jupiter": J, "mars": M, "objects_05": {k: len(R[k][F5]["objects"]) for k in RUNS},
        "camera_time_05": hm(no5["true_time"]), "true_time_05": hm(full5["true_time"]),
        "raw_clock_field_alt_deg": round(raw_alt, 1),
        "neither_04": {"objects": len(ne4["objects"]), "same_confidence": same4, "uranus": ura},
        "neither_05": {"objects": len(ne5["objects"])},
        "confidence_steps": {c: k for c, _, k in conf_steps},
        "group_steps": [k for _, _, k, _ in g_steps],
    }

    def loc_card(state):
        return panel("1. Location", [
            ("field", "Site for this folder", site_txt if state else "(not set)", "changed" if state else "empty"),
            ("note", "Paste coordinates, a Google Maps link, or use the photo's GPS in Facet."),
            ("gap", 10),
            ("big", f"Jupiter {J['full' if state else 'nosite']}\nMars {M['full' if state else 'nosite']}",
             GOOD if state else HI),
        ])

    def time_card(state):
        return panel("2. Time", [
            ("field", "Camera clock (EXIF)", hm(no5["true_time"]), "set"),
            ("field", "Offset", offset_txt if state else "(empty: clock right)", "changed" if state else "empty"),
            ("field", "Real time", hm(full5["true_time"] if state else no5["true_time"]), "changed" if state else "set"),
            ("note", f"Location: {SITE['name']} (set)"),
            ("big", f"{len(full5['objects']) if state else 0} objects", GOOD if state else WARN),
        ])

    def fuzzy_card():
        return panel("Neither set", [
            ("field", "1. Location", "(not set)", "empty"),
            ("field", "2. Time offset", "(not set)", "empty"),
            ("big", f"{len(ne4['objects'])} objects", GOOD),
            ("note", f"{same4} of {tot4} at the same confidence as with site and offset.", WHITE),
            ("note", f"Only Uranus moves: {ura[0]} → {ura[1]} (hidden at ≥ 65).", HI),
        ])

    def conf_card(c, k, prev):
        return panel("5. Sky labels", [
            ("switch", "Sky labels on this photo: On", True),
            ("gap", 6),
            ("slider", "Only names at confidence ≥", c),
            ("gap", 6),
            ("big", f"{k} names shown", HI),
            ("note", " → ".join(str(x) for x in prev) if len(prev) > 1 else "", MUTED),
            ("note", f"{tot4} objects found in this photo; stars are off by default."),
        ])

    def group_card(g, scores, changed):
        rows = [("switch", "Sky labels on this photo: On", True), ("gap", 2)]
        rows += [("check", lab, g[k], k in changed) for k, lab in GROUPS]
        rows += [("gap", 4), ("check", "show scores", scores, scores), ("gap", 6)]
        return panel("5. Sky labels", rows)

    def scenes():
        title = [("astro-id", font(84, True), WHITE),
                 ("names what is in your night-sky photos", font(40), (170, 215, 255))]
        base = fview(orig4, 0.5, 0.55, 1.0)
        tc = title_card(base, title)
        for i in range(n(0.5)):                                           # 1. title, 2 s
            yield title_card(base, title, ease((i + 1) / n(0.5)))
        yield from hold(tc, 1.5)
        plain = bar(fview(orig4, 0.5, 0.55, 1.0), "A night-sky photo, no labels")
        yield from xfade(tc, plain, 0.4)                                   # 2. result, 5 s
        yield from hold(plain, 1.3)
        cap = f"astro-id names what is in it: {tot4} objects"
        sub = "constellations, planets, star clusters, nebulae, named stars"
        yield from xfade(plain, bar(fview(lab4, 0.5, 0.55, 1.0), cap, sub), 1.0)
        for i in range(n(2.3)):
            t = ease(i / n(2.3))
            yield bar(fview(lab4, 0.5, 0.55 - 0.15 * t, 1.0 + 0.6 * t), cap, sub)
        a = layout(d_nosite, loc_card(False), f"No location: Jupiter {J['nosite']} · Mars {M['nosite']}",
                   "without a site, planets are capped at 79 and get no horizon check", "detail, Jupiter and Mars")
        b = layout(d_full, loc_card(True), f"Location set: Jupiter {J['full']} · Mars {M['full']}",
                   "placed for the site and checked against the horizon", "detail, Jupiter and Mars")
        yield from xfade(bar(fview(lab4, 0.5, 0.40, 1.6), cap, sub), a, 0.4)   # 3. location, 5 s
        yield from hold(a, 1.9)
        yield from xfade(a, b, 0.5)
        yield from hold(b, 2.2)
        loc_end = b
        a = layout(d_raw, time_card(False), f"Camera clock as recorded ({hm(no5['true_time'])}): 0 objects",
                   f"then, the centre of this field was {abs(round(raw_alt))}° below the horizon at the site",
                   "same detail, nothing named")
        b = layout(d_full, time_card(True), f"Clock offset {CFG['clock_offset']}: Jupiter {J['full']} · Mars {M['full']}",
                   f"real time {hm(full5['true_time'])} · {len(full5['objects'])} objects in the frame",
                   "detail, Jupiter and Mars")
        yield from xfade(loc_end, a, 0.4)                                   # 4. time, 6 s
        yield from hold(a, 2.6)
        yield from xfade(a, b, 0.5)
        yield from hold(b, 2.5)
        f1 = layout(o_neither, fuzzy_card(), "Location and time can be approximate",
                    "an estimated site and an offset with a ± tolerance are enough", "no site, no offset")
        f2 = layout(o_neither, fuzzy_card(), "Stars, constellations, deep-sky objects need neither",
                    f"they come from the stars: {same4} of {tot4} names at the same confidence", "no site, no offset")
        yield from xfade(b, f1, 0.4)                                        # 5. fuzzy is fine, 5 s
        yield from hold(f1, 2.1)
        yield from xfade(f1, f2, 0.3)
        yield from hold(f2, 2.2)
        prev, last = [], f2
        for i, (c, pic, k) in enumerate(conf_steps):                        # 6. confidence, 6 s
            prev.append(k)
            fr = layout(pic, conf_card(c, k, prev), f"Only names at confidence ≥ {c}: {k} names",
                        "labels thin out as the slider rises", "Orion rising, whole frame")
            yield from xfade(last, fr, 0.3)
            yield from hold(fr, 1.7)
            last = fr
        prevg, changed_caps = set(), []
        for i, (g, pic, k, scores) in enumerate(g_steps):                   # 7. what is shown, 6 s
            on = {key for key, v in g.items() if v}
            changed = set() if scores else on - prevg
            prevg = on
            if scores:
                txt = f"show scores: confidence after each name ({k} names)"
            elif i == 0:
                txt = f"Constellations only: {k} names"
            else:
                txt = "+ " + ", ".join(lab for key, lab in GROUPS if key in changed) + f": {k} names"
            fr = layout(pic, group_card(g, scores, changed), txt, "groups on and off, same photo",
                        "Orion rising, whole frame")
            yield from xfade(last, fr, 0.25)
            yield from hold(fr, 0.95)
            last = fr
        where = [("Where it runs", font(54, True), WHITE),
                 ("From the command line", font(36), (170, 215, 255)),
                 ("astro-id label photo.jpg", font(40, mono=True), HI),
                 ("or inside the Facet photo library (the Astro panel)", font(36), (170, 215, 255))]
        wc = title_card(fview(lab4, 0.5, 0.55, 1.0), where)
        yield from xfade(last, wc, 0.4)                                     # 8. where it runs, 3 s
        yield from hold(wc, 2.6)
        end = [("astro-id", font(76, True), WHITE),
               ("MIT · github.com/raysalem/astro-id", font(42), (170, 215, 255))]
        ec = title_card(fview(lab4, 0.5, 0.55, 1.0), end)
        yield from xfade(wc, ec, 0.4)                                       # 9. end, 2 s
        yield from hold(ec, 1.6)

    def gif():
        """README loop (~9.5 s): plain -> labeled -> confidence slider -> groups."""
        plain = bar(fview(orig4, 0.5, 0.55, 1.0), "A night-sky photo, no labels")
        labd = bar(fview(lab4, 0.5, 0.55, 1.0), f"astro-id names what is in it: {tot4} objects")
        yield from hold(plain, 1.2)
        yield from xfade(plain, labd, 0.6)
        yield from hold(labd, 1.0)
        prev, last = [], labd
        for c, pic, k in conf_steps:
            prev.append(k)
            fr = layout(pic, conf_card(c, k, prev), f"Only names at confidence ≥ {c}: {k} names")
            yield from xfade(last, fr, 0.25)
            yield from hold(fr, 0.95)
            last = fr
        prevg = set()
        for i in (0, 1, 3):
            g, pic, k, _ = g_steps[i]
            on = {key for key, v in g.items() if v}
            changed, prevg = on - prevg, on
            txt = (f"Constellations only: {k} names" if i == 0 else
                   "+ " + ", ".join(lab for key, lab in GROUPS if key in changed) + f": {k} names")
            fr = layout(pic, group_card(g, False, changed), txt)
            yield from xfade(last, fr, 0.25)
            yield from hold(fr, 0.9)
            last = fr

    return scenes, gif, numbers


def encode(ff, args, out, frames):
    p = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *args, str(out)], stdin=subprocess.PIPE)
    k = 0
    for f in frames:
        assert f.size == (W, H), f.size
        p.stdin.write(f.tobytes())
        k += 1
    p.stdin.close()
    if p.wait():
        sys.exit("ffmpeg failed")
    return k


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reuse", action="store_true", help="reuse the solves in build/demo from the last run")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_exe()
    scenes, gif, numbers = build(solve_runs(a.reuse))
    print(json.dumps(numbers, indent=1))
    (WORK / "numbers.json").write_text(json.dumps(numbers, indent=1), encoding="utf-8")
    k = encode(ff, ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "slow",
                    "-movflags", "+faststart", "-an"], OUT / "demo.mp4", scenes())
    print("demo.mp4:", k, "frames,", round(k / FPS, 1), "s")
    # GIF: 640 px, 8 fps, 64 colours, no dithering (dithered star-field noise does not compress)
    k = encode(ff, ["-vf", "fps=8,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=none:diff_mode=rectangle", "-loop", "0"], OUT / "demo.gif", gif())
    print("demo.gif:", round(k / FPS, 1), "s")
    for f in ("demo.mp4", "demo.gif"):
        print(f, round((OUT / f).stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
