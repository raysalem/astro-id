"""Build docs/img/demo.mp4, demo.webp and demo-poster.jpg from the example photos.

Run from the repo root:
    uv run --with imageio-ffmpeg python tools/make_demo.py
(ffmpeg on PATH is used if imageio-ffmpeg is not installed.)
Captions come from examples/expected.json only. It stores no per-object score, so the
confidence shown is its stated assert threshold: tags >= 85, planets/Moon >= 80.
"""
import json, shutil, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
EX, OUT = ROOT / "examples", ROOT / "docs" / "img"
W, H, FPS = 1280, 720, 24
SRC_W = 3200  # working width for sharp zooms

exp = json.loads((EX / "expected.json").read_text(encoding="utf-8"))["files"]


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


def load(path):
    im = Image.open(path).convert("RGB")
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
lab = {k: load(EX / "annotated" / (Path(k).stem + "_astro.jpg")) for k in exp}

M45 = (0.445, 0.278)  # M45 Pleiades in frame 04, normalised
BASE = (0.5, 0.62)

CAP = {
    "01-cassiopeia-perseus.jpg": "Cassiopeia · Perseus · Auriga",
    "02-milky-way-cepheus.jpg": "Cepheus · M39 · Triangulum",
    "03-taurus-perseus-uranus.jpg": "M45 Pleiades · Uranus · Perseus",
    "05-dawn-jupiter-mars.jpg": "Jupiter · Mars",
}
for k, v in CAP.items():  # truth check: every caption item must be a tag in expected.json
    tags = " ".join(exp[k]["tags"] + exp[k]["borderline"]).lower()
    for part in v.split(" · "):
        assert part.lower().split()[0] in tags, (k, part)
assert "m45 pleiades" in exp[f4]["tags"]
THR_TAG, THR_PLANET = "confidence ≥ 85", "confidence ≥ 80"


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
        yield bar(view(lab[f4], cx, cy, 1.25 + 2.55 * t), "M45 Pleiades", THR_TAG)
    for k in ["01-cassiopeia-perseus.jpg", "02-milky-way-cepheus.jpg",
              "03-taurus-perseus-uranus.jpg", "05-dawn-jupiter-mars.jpg"]:  # 14-18 montage
        thr = THR_PLANET if k.startswith("05") else THR_TAG
        for i in range(n(1)):
            t = i / n(1)
            z = 1.0 + 0.05 * t
            a, b = view(orig[k], 0.5, 0.5, z), view(lab[k], 0.5, 0.5, z)
            yield bar(mix(a, b, (t - 0.25) / 0.35), CAP[k], thr if t > 0.5 else "")
    end = [("astro-id", font(72, True), (255, 255, 255)),
           ("MIT · github.com/raysalem/astro-id", font(40), (170, 215, 255))]
    bg = view(lab[f4], *BASE, 1.0)
    for i in range(n(2)):  # 18-20 end card
        yield card(bg, end, ease(i / n(0.4)))


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
    # animated WebP (a GIF of this star field is ~20-48 MB, far over budget)
    encode(ff, ["-vf", "fps=12,scale=900:-1:flags=lanczos", "-c:v", "libwebp", "-quality", "60",
                "-loop", "0", "-an"], OUT / "demo.webp", fr)
    Image.frombytes("RGB", (W, H), fr[int(10.5 * FPS)]).save(OUT / "demo-poster.jpg", quality=88)
    for f in ("demo.mp4", "demo.webp", "demo-poster.jpg"):
        print(f, round((OUT / f).stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
