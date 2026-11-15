"""Build docs/img/demo.mp4 and demo.gif from the example photos.

Run from the repo root:
    uv run --with imageio-ffmpeg python tools/make_demo.py
(ffmpeg on PATH is used if imageio-ffmpeg is not installed.)
Captions and the confidence numbers come from examples/expected.json ("scores": the confidence each
listed tag had when the example set was solved). demo.gif is a short loop (original -> labeled -> zoom)
for inline display in the README; demo.mp4 is the full 20 s clip.
"""
import json, shutil, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
EX, OUT = ROOT / "examples", ROOT / "docs" / "img"
W, H, FPS = 1280, 720, 24
SRC_W = 3200  # working width for sharp zooms
LEGEND = (0.215, 0.86)  # legend box of a labeled copy: x < 21.5 % of the width, y > 86 % of the height

exp = json.loads((EX / "expected.json").read_text(encoding="utf-8"))["files"]


def score(k, tag):
    """Confidence of an asserted tag in example k, from expected.json (fails loudly if it is not asserted)."""
    assert tag in exp[k]["tags"], (k, tag)
    return exp[k]["scores"][tag]


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        p = shutil.which("ffmpeg")
        if not p:
            sys.exit("need ffmpeg: uv run --with imageio-ffmpeg python tools/make_demo.py")
        return p


def font(size, bold=False):
    for n in (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]) + ["DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default()


def load(path, under=None):
    im = Image.open(path).convert("RGB")
    if under is not None:  # cover the solver's legend box (bottom-left) with the same pixels of the original
        o = Image.open(under).convert("RGB")
        box = (0, round(im.height * LEGEND[1]), round(im.width * LEGEND[0]), im.height)
        im.paste(o.crop(box), box[:2])
    return im.resize((SRC_W, round(im.height * SRC_W / im.width)), Image.LANCZOS)


def view(im, cx, cy, zoom):
    """Normalised view (cx, cy, zoom) -> WxH frame; same view for original and labeled."""
    cw = SRC_W / zoom
    ch = cw * H / W
    x0 = min(max(cx * im.width - cw / 2, 0), im.width - cw)
    y0 = min(max(cy * im.height - ch / 2, 0), im.height - ch)
    return im.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))


def bar(img, text, sub=None):
    img = img.convert("RGBA")
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle((0, H - 112, W, H), fill=(5, 8, 16, 190))
    d.text((40, H - 96), text, font=font(36, True), fill=(255, 255, 255, 255))
    if sub:
        d.text((40, H - 48), sub, font=font(22), fill=(150, 230, 170, 255))
    return Image.alpha_composite(img, ov).convert("RGB")


def card(bg, lines, alpha=1.0):
    dark = Image.new("RGB", (W, H), (4, 6, 14))
    base = Image.blend(dark, bg.filter(ImageFilter.GaussianBlur(6)), 0.25)
    d = ImageDraw.Draw(base)
    y = H // 2 - 70
    for t, f, col in lines:
        d.text(((W - d.textlength(t, font=f)) / 2, y), t, font=f, fill=col)
        y += f.size + 22
    return Image.blend(dark, base, alpha) if alpha < 1 else base


def ease(t):
    t = min(max(t, 0), 1)
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    return Image.blend(a, b, ease(t))


f4 = "04-orion-rising.jpg"
orig = {k: load(EX / k) for k in exp}
lab = {k: load(EX / "annotated" / (Path(k).stem + "_astro.jpg"), under=EX / k) for k in exp}

M45 = (0.445, 0.278)  # M45 Pleiades in frame 04, normalised
BASE = (0.5, 0.62)

# montage: (label, tag) per frame, and the view (cx, cy, zoom) to show it at
CAP = {
    "01-cassiopeia-perseus.jpg": ([("Cassiopeia", "cassiopeia"), ("Perseus", "perseus"),
                                   ("h Persei", "ngc 869 h persei cluster")], (0.5, 0.5, 1.0)),
    "02-milky-way-cepheus.jpg": ([("Cepheus", "cepheus"), ("M39", "m39"),
                                  ("North America Neb.", "ngc 7000 north america nebula")], (0.5, 0.5, 1.0)),
    "03-dawn-orion-sirius.jpg": ([("Canis Major", "canis major"), ("Winter Triangle", "winter triangle"),
                                  ("Orion's Belt", "orion's belt")], (0.6, 0.45, 1.25)),
    "05-dawn-jupiter-mars.jpg": ([("Jupiter", "jupiter"), ("Mars", "mars"),
                                  ("Sickle of Leo", "sickle of leo")], (0.47, 0.42, 2.0)),
}
CAPTION = {k: " · ".join(f"{lbl} {score(k, tag)}" for lbl, tag in items) for k, (items, _) in CAP.items()}
M45_TEXT = f"M45 Pleiades · confidence {score(f4, 'm45 pleiades')}"
CONF_SUB = "confidence 0-100, from the solved star field"


def frames():
    n = lambda s: int(s * FPS)
    title = [("astro-id", font(84, True), (255, 255, 255)),
             ("names what is in your night-sky photos", font(38), (170, 215, 255))]
    for i in range(n(2)):  # 0-2 title card
        yield card(view(orig[f4], *BASE, 1.0), title, ease(i / n(0.6)))
    for i in range(n(4)):  # 2-6 original, slow push-in
        t = i / n(4)
        im = view(orig[f4], BASE[0], BASE[1] + (M45[1] - BASE[1]) * 0.15 * t, 1.0 + 0.15 * t)
        if i < n(0.4):
            im = mix(card(view(orig[f4], *BASE, 1.0), title), im, i / n(0.4))
        yield bar(im, "A night-sky photo, no labels", "plate-solving the star field ...") if t > .2 else im
    for i in range(n(4)):  # 6-10 crossfade to the labeled copy
        t = i / n(4)
        z, cy = 1.15 + 0.1 * t, BASE[1] + (M45[1] - BASE[1]) * (0.15 + 0.1 * t)
        a, b = view(orig[f4], BASE[0], cy, z), view(lab[f4], BASE[0], cy, z)
        yield bar(mix(a, b, (t - 0.1) / 0.5), "Constellations, stars, planets, deep-sky objects",
                  "named from the solved sky position")
    for i in range(n(4)):  # 10-14 zoom on M45
        t = ease(i / n(4))
        cx = BASE[0] + (M45[0] - BASE[0]) * t
        cy = BASE[1] + (M45[1] - BASE[1]) * (0.25 + 0.75 * t)
        yield bar(view(lab[f4], cx, cy, 1.25 + 2.55 * t), M45_TEXT, CONF_SUB)
    for k, (_, (vx, vy, vz)) in CAP.items():  # 14-18 montage
        for i in range(n(1)):
            t = i / n(1)
            z = vz * (1.0 + 0.05 * t)
            a, b = view(orig[k], vx, vy, z), view(lab[k], vx, vy, z)
            yield bar(mix(a, b, (t - 0.25) / 0.35), CAPTION[k] if t > 0.3 else "", CONF_SUB if t > 0.5 else "")
    end = [("astro-id", font(72, True), (255, 255, 255)),
           ("MIT · github.com/raysalem/astro-id", font(40), (170, 215, 255))]
    bg = view(lab[f4], *BASE, 1.0)
    for i in range(n(2)):  # 18-20 end card
        yield card(bg, end, ease(i / n(0.4)))


def gif_frames():
    """Short loop for the README: original -> labeled -> zoom on M45 -> hold (7.5 s)."""
    n = lambda s: int(s * FPS)
    for i in range(n(1.5)):
        yield bar(view(orig[f4], *BASE, 1.0), "A night-sky photo, no labels")
    for i in range(n(1.0)):
        yield bar(mix(view(orig[f4], *BASE, 1.0), view(lab[f4], *BASE, 1.0), i / n(1.0)),
                  "astro-id names what is in it")
    for i in range(n(1.0)):
        yield bar(view(lab[f4], *BASE, 1.0), "astro-id names what is in it")
    for i in range(n(2.5)):
        t = ease(i / n(2.5))
        cx = BASE[0] + (M45[0] - BASE[0]) * t
        cy = BASE[1] + (M45[1] - BASE[1]) * t
        yield bar(view(lab[f4], cx, cy, 1.0 + 2.8 * t), M45_TEXT, CONF_SUB)
    last = bar(view(lab[f4], *M45, 3.8), M45_TEXT, CONF_SUB)
    for i in range(n(1.5)):
        yield last


def encode(ff, args, out, raw):
    p = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *args, str(out)],
                         stdin=subprocess.PIPE)
    for b in raw:
        p.stdin.write(b)
    p.stdin.close()
    if p.wait():
        sys.exit("ffmpeg failed")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_exe()
    print("ffmpeg:", ff)
    fr = [f.tobytes() for f in frames()]
    print(len(fr), "frames,", len(fr) / FPS, "s")
    encode(ff, ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "slow",
                "-movflags", "+faststart", "-an"], OUT / "demo.mp4", fr)
    # GIF: 640 px, 8 fps, 64 colours, no dithering (dithered star-field noise does not compress)
    encode(ff, ["-vf", "fps=8,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];"
                "[b][p]paletteuse=dither=none:diff_mode=rectangle", "-loop", "0"],
           OUT / "demo.gif", [f.tobytes() for f in gif_frames()])
    for f in ("demo.mp4", "demo.gif"):
        print(f, round((OUT / f).stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
