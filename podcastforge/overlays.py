"""Animated overlays rendered with Pillow into alpha .mov files (qtrle) that ffmpeg composites later.

Kinds
  circle   photo (or short video clip) inside an animated ring that pops in beside the speaker
  stat     lower-third stat card with a count-up number
  kinetic  big centred word-by-word text
  name     guest / host lower third
  chapter  top-left chapter tag
  title    full-frame opaque title card (logo + two lines + optional pre-rendered credit image)
"""
import glob
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FPS = 30
FONT_DIRS = ["C:/Windows/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
             os.path.expanduser("~/Library/Fonts"), "/usr/share/fonts", os.path.expanduser("~/.fonts"),
             os.path.expanduser("~/.local/share/fonts")]


class Style:
    """Accent colours and font lookup, picklable so overlays can render in worker processes."""

    def __init__(self, accent=(255, 153, 0), accent2=(255, 210, 64), fonts=None, extra_dirs=()):
        self.acc, self.acc2 = tuple(accent), tuple(accent2)
        self.fonts = fonts or {"display": "impact.ttf", "title": "bahnschrift.ttf", "body": "segoeuib.ttf"}
        self.dirs = list(extra_dirs) + FONT_DIRS

    @classmethod
    def from_project(cls, P):
        s = P.cfg["style"]
        return cls(s["accent"], s["accent2"], s["fonts"], [P.path("fonts")])

    def find(self, name):
        if os.path.isabs(name) and os.path.exists(name):
            return name
        for d in self.dirs:
            for pat in (os.path.join(d, name), os.path.join(d, "**", name)):
                hit = glob.glob(pat, recursive=True)
                if hit:
                    return hit[0]
        return None

    def font(self, role, size, var=None):
        path = self.find(self.fonts.get(role, role))
        if path is None:
            for fallback in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
                path = self.find(fallback)
                if path:
                    break
        try:
            f = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
        except Exception:
            f = ImageFont.load_default(size)
        if var:
            try:
                f.set_variation_by_name(var)
            except Exception:
                pass
        return f


def ease_out_back(x, k=1.7):
    x = min(max(x, 0), 1) - 1
    return 1 + (k + 1) * x ** 3 + k * x ** 2


def ease_out(x):
    x = min(max(x, 0), 1)
    return 1 - (1 - x) ** 3


def ease_in(x):
    x = min(max(x, 0), 1)
    return x ** 3


def writer(path, w, h, opaque=False):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-"]
    args += (["-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p"] if opaque else ["-c:v", "qtrle"]) + [path]
    return subprocess.Popen(args, stdin=subprocess.PIPE)


def cover(img, w, h):
    return ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.42))


def framed(img, size, focus, fit):
    """Square crop around `focus` (fractions of the image) showing `fit` x the short side.
    fit > 1 shows more than fits: the photo is letterboxed onto a blurred copy of itself."""
    w, h = img.size
    side = min(w, h) * fit
    if fit <= 1:
        cx = min(max(focus[0] * w, side / 2), w - side / 2)
        cy = min(max(focus[1] * h, side / 2), h - side / 2)
        return img.crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))).resize((size, size), Image.LANCZOS)
    bg = cover(img, size, size).filter(ImageFilter.GaussianBlur(18))
    bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.35)
    k = size / side
    fg = img.resize((int(w * k), int(h * k)), Image.LANCZOS)
    bg.paste(fg, (int(size / 2 - focus[0] * fg.width), int(size / 2 - focus[1] * fg.height)))
    return bg


def video_frames(path, start, dur, size, pre=""):
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", path, "-vf",
                        pre + f"fps={FPS},scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    n = len(p.stdout) // (size * size * 3)
    a = np.frombuffer(p.stdout[: n * size * size * 3], np.uint8).reshape(n, size, size, 3)
    return [Image.fromarray(f) for f in a]


def circle(spec, out, S):
    D, R, PAD = 440, 9, 70
    cw = D + 2 * PAD
    cap = spec.get("caption")
    capf = S.font("body", 30)
    ch = cw + (80 if cap else 0)
    dur = spec.get("dur", 4.5)
    n = int(round(dur * FPS))
    ss = 3
    m = Image.new("L", (D * ss, D * ss), 0)
    ImageDraw.Draw(m).ellipse((0, 0, D * ss - 1, D * ss - 1), fill=255)
    mask = m.resize((D, D), Image.LANCZOS)
    base = Image.new("RGBA", (cw, cw), (0, 0, 0, 0))
    sh = Image.new("L", (cw, cw), 0)
    ImageDraw.Draw(sh).ellipse((PAD - R + 6, PAD - R + 14, PAD + D + R + 6, PAD + D + R + 14), fill=170)
    base.putalpha(sh.filter(ImageFilter.GaussianBlur(18)))
    glow = Image.new("RGBA", (cw, cw), S.acc + (0,))
    gl = Image.new("L", (cw, cw), 0)
    ImageDraw.Draw(gl).ellipse((PAD - R - 4, PAD - R - 4, PAD + D + R + 4, PAD + D + R + 4), outline=200, width=R + 8)
    glow.putalpha(gl.filter(ImageFilter.GaussianBlur(14)))
    base = Image.alpha_composite(base, glow)
    yy, xx = np.mgrid[0:cw * ss, 0:cw * ss].astype(np.float32) / ss - cw / 2
    ang = (np.arctan2(yy, xx) + math.pi) / (2 * math.pi)
    t = (np.sin(ang * 2 * math.pi * 2) + 1) / 2
    a, b = np.array(S.acc, np.float32), np.array(S.acc2, np.float32)
    ringc = np.zeros((cw * ss, cw * ss, 4), np.uint8)
    ringc[..., :3] = (a[None, None] * (1 - t[..., None]) + b[None, None] * t[..., None]).astype(np.uint8)
    rr = np.hypot(xx, yy)
    ringc[..., 3] = ((rr <= D / 2 + R) & (rr >= D / 2 - 1)).astype(np.uint8) * 255
    ring = Image.fromarray(ringc, "RGBA").resize((cw, cw), Image.LANCZOS)

    frames = None
    if spec.get("video"):
        frames = video_frames(spec["video"], spec.get("vstart", 0), dur + 0.2, int(D * 1.1), spec.get("vpre", ""))
    else:
        img = ImageOps.exif_transpose(Image.open(spec["image"])).convert("RGB")
        big = framed(img, int(D * 1.1), spec.get("focus", (0.5, 0.4)), spec.get("fit", 1.0))
    w = writer(out, cw, ch)
    for i in range(n):
        tt = i / FPS
        s = ease_out_back(tt / 0.45) if tt < 0.45 else 1.0
        out_t = dur - tt
        if out_t < 0.35:
            s = 1 - ease_in(1 - out_t / 0.35)
        if frames:
            fr = frames[min(i, len(frames) - 1)]
            o = (fr.width - D) // 2
            content = fr.crop((o, o, o + D, o + D))
        else:
            z = 1.0 + 0.1 * (tt / dur)                       # gentle push-in on the faces
            src = big.resize((int(D * 1.1 * z),) * 2, Image.LANCZOS)
            o = (src.width - D) // 2
            content = src.crop((o, o, o + D, o + D))
        frame = base.copy()
        layer = Image.new("RGBA", (cw, cw), (0, 0, 0, 0))
        layer.paste(content, (PAD, PAD), mask)
        frame = Image.alpha_composite(Image.alpha_composite(frame, layer), ring)
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if s > 0.01:
            fs = frame.resize((max(1, int(cw * s)), max(1, int(cw * s))), Image.BILINEAR)
            canvas.alpha_composite(fs, ((cw - fs.width) // 2, (cw - fs.height) // 2))
        if cap:
            ca = ease_out((tt - 0.35) / 0.35) * (1 - ease_in(1 - out_t / 0.3) if out_t < 0.3 else 1)
            if ca > 0:
                d = ImageDraw.Draw(canvas)
                tw = d.textlength(cap, font=capf)
                x0, y0 = (cw - tw) / 2 - 18, cw - 52 + (1 - ca) * 20
                pill = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                pd = ImageDraw.Draw(pill)
                pd.rounded_rectangle((x0, y0, x0 + tw + 36, y0 + 50), 25, fill=(12, 12, 16, int(215 * ca)), outline=S.acc + (int(255 * ca),), width=2)
                pd.text((x0 + 18, y0 + 7), cap, font=capf, fill=(255, 255, 255, int(255 * ca)))
                canvas = Image.alpha_composite(canvas, pill)
        w.stdin.write(canvas.tobytes())
    w.stdin.close(); w.wait()
    return cw, ch


def stat(spec, out, S):
    dur = spec.get("dur", 4.5)
    n = int(dur * FPS)
    big, sub = S.font("title", 92, "Bold"), S.font("body", 34)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    num = spec.get("count")                                   # [value, decimals, prefix, suffix]
    full = spec.get("title") or f"{num[2]}{num[0]:.{num[1]}f}{num[3]}"
    W_, H_ = int(max(d0.textlength(full, font=big), d0.textlength(spec["sub"], font=sub)) + 110), 230
    w = writer(out, W_, H_)
    for i in range(n):
        tt = i / FPS
        ot = dur - tt
        wipe = ease_out(tt / 0.45) if ot > 0.4 else 1 - ease_in(1 - ot / 0.4)
        im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        pw = int(W_ * wipe)
        if pw > 12:
            d.rounded_rectangle((0, 20, pw, H_ - 10), 18, fill=(10, 10, 14, 215))
            d.rectangle((0, 20, 12, H_ - 10), fill=S.acc)
        ta = ease_out((tt - 0.25) / 0.35) * (min(1, ot / 0.35))
        if ta > 0:
            txt = f"{num[2]}{num[0] * ease_out((tt - 0.25) / 1.2):.{num[1]}f}{num[3]}" if num else spec["title"]
            lay = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
            td = ImageDraw.Draw(lay)
            td.text((44, 34 + (1 - ta) * 25), txt, font=big, fill=S.acc2 + (int(255 * ta),))
            td.text((48, 150 + (1 - ta) * 15), spec["sub"], font=sub, fill=(255, 255, 255, int(255 * ta)))
            mask = Image.new("L", (W_, H_), 0)
            ImageDraw.Draw(mask).rectangle((0, 0, pw, H_), fill=255)
            im.paste(lay, (0, 0), Image.composite(lay.getchannel("A"), mask, mask))
        w.stdin.write(im.tobytes())
    w.stdin.close(); w.wait()
    return W_, H_


def kinetic(spec, out, S):
    W_, H_ = spec.get("w", 1920), spec.get("h", 1080)
    FS = spec.get("size", 150)
    dur = spec.get("dur", 3.0)
    n = int(dur * FPS)
    f = S.font("display", FS)
    words = spec["text"].split()
    hl = {x.upper() for x in spec.get("highlight", [])}
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    ws = [d0.textlength(x + " ", font=f) for x in words]
    lines, cur, cw_ = [], [], 0
    for i, x in enumerate(words):
        if cur and cw_ + ws[i] > W_ - 200:
            lines.append(cur); cur, cw_ = [], 0
        cur.append(i); cw_ += ws[i]
    lines.append(cur)
    pos = {}
    y = spec.get("cy", H_ / 2) - len(lines) * FS * 1.13 / 2
    for ln in lines:
        x = (W_ - sum(ws[i] for i in ln)) / 2
        for i in ln:
            pos[i] = (x, y); x += ws[i]
        y += FS * 1.13
    w = writer(out, W_, H_)
    for k in range(n):
        tt = k / FPS
        ot = dur - tt
        im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
        dim = min(1, tt / 0.2) * min(1, ot / 0.25)
        im.paste((0, 0, 0, int(90 * dim)), (0, 0, W_, H_))
        for i, x in enumerate(words):
            st = 0.08 + i * 0.13
            a = ease_out_back((tt - st) / 0.3) if tt > st else 0
            if a <= 0:
                continue
            al = int(255 * min(1, a) * min(1, ot / 0.25))
            col = S.acc2 if x.strip(".,!?").upper() in hl else (255, 255, 255)
            sz = int(FS * (0.6 + 0.4 * a))
            ff = S.font("display", max(10, sz))
            px, py = pos[i]
            off = (FS - sz) / 2
            layer = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((px + 6 + off, py + 8 + off), x, font=ff, fill=(0, 0, 0, int(al * 0.6)))
            layer = layer.filter(ImageFilter.GaussianBlur(4))
            ImageDraw.Draw(layer).text((px + off, py + off), x, font=ff, fill=col + (al,))
            im = Image.alpha_composite(im, layer)
        w.stdin.write(im.tobytes())
    w.stdin.close(); w.wait()
    return W_, H_


def name(spec, out, S):
    W_, H_ = 1100, 200
    dur = spec.get("dur", 5.0)
    n = int(dur * FPS)
    big, sub = S.font("title", 64, "Bold"), S.font("body", 30)
    w = writer(out, W_, H_)
    for i in range(n):
        tt, ot = i / FPS, dur - i / FPS
        p = ease_out(tt / 0.5) if ot > 0.45 else 1 - ease_in(1 - ot / 0.45)
        im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        tw = max(d.textlength(spec["name"], font=big), d.textlength(spec["role"], font=sub)) + 80
        bw = int(tw * p)
        d.rectangle((0, 30, 8, 170), fill=S.acc + (int(255 * min(1, p * 3)),))
        if bw > 10:
            d.rectangle((8, 30, 8 + bw, 118), fill=(10, 10, 14, 220))
            d.rectangle((8, 118, 8 + bw, 170), fill=S.acc + (235,))
            lay = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            ld.text((34, 36), spec["name"], font=big, fill=(255, 255, 255, 255))
            ld.text((34, 124), spec["role"], font=sub, fill=(15, 15, 15, 255))
            m = Image.new("L", (W_, H_), 0)
            ImageDraw.Draw(m).rectangle((0, 0, 8 + bw, H_), fill=255)
            im.paste(lay, (0, 0), Image.composite(lay.getchannel("A"), m, m))
        w.stdin.write(im.tobytes())
    w.stdin.close(); w.wait()
    return W_, H_


def chapter(spec, out, S):
    W_, H_ = 900, 120
    dur = spec.get("dur", 4.0)
    n = int(dur * FPS)
    sm, big = S.font("body", 24), S.font("title", 44, "Bold")
    w = writer(out, W_, H_)
    for i in range(n):
        tt, ot = i / FPS, dur - i / FPS
        a = ease_out(tt / 0.4) * min(1, ot / 0.4)
        im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        x = -40 * (1 - a)
        tw = d.textlength(spec["title"], font=big) + 60
        d.rounded_rectangle((x, 10, x + tw, 110), 14, fill=(10, 10, 14, int(200 * a)))
        d.text((x + 28, 18), spec["label"], font=sm, fill=S.acc + (int(255 * a),))
        d.text((x + 26, 48), spec["title"], font=big, fill=(255, 255, 255, int(255 * a)))
        w.stdin.write(im.tobytes())
    w.stdin.close(); w.wait()
    return W_, H_


def title(spec, out, S):
    W_, H_ = 1920, 1080
    dur = spec.get("dur", 3.0)
    n = int(dur * FPS)
    logo = Image.open(spec["logo"]).convert("RGBA") if spec.get("logo") else None
    if logo:
        logo.thumbnail((520, 520))
    t1, t2 = S.font("title", 46, "SemiBold"), S.font("title", 70, "Bold")
    w = writer(out, W_, H_, opaque=True)
    yy, xx = np.mgrid[0:H_, 0:W_]
    vig = np.clip(1 - np.hypot((xx - W_ / 2) / W_, (yy - H_ / 2) / H_) * 1.4, 0, 1)
    bg = np.zeros((H_, W_, 4), np.uint8)
    bg[..., 0] = (28 * vig).astype(np.uint8); bg[..., 1] = (16 * vig).astype(np.uint8); bg[..., 2] = (8 * vig).astype(np.uint8)
    bg[..., 3] = 255
    bgi = Image.fromarray(bg, "RGBA")
    cr = None
    if spec.get("credit"):
        cr = Image.open(spec["credit"]).convert("RGBA")
        cr = cr.resize((760, int(cr.height * 760 / cr.width)), Image.LANCZOS)
    for i in range(n):
        tt, ot = i / FPS, dur - i / FPS
        im = bgi.copy()
        s = 0.85 + 0.15 * ease_out_back(tt / 0.6) + 0.03 * tt
        a = min(1, tt / 0.25) * min(1, ot / 0.3)
        if logo:
            lg = logo.resize((int(logo.width * s), int(logo.height * s)), Image.LANCZOS)
            lg.putalpha(lg.getchannel("A").point(lambda v: int(v * a)))
            im.alpha_composite(lg, ((W_ - lg.width) // 2, 360 - lg.height // 2))
        ta = ease_out((tt - 0.4) / 0.5) * min(1, ot / 0.3)
        d = ImageDraw.Draw(im)
        for txt, f, y, col in ((spec.get("line1", ""), t1, 690, S.acc), (spec.get("line2", ""), t2, 760, (255, 255, 255))):
            tw = d.textlength(txt, font=f)
            d.text(((W_ - tw) / 2, y + (1 - ta) * 20), txt, font=f, fill=col + (int(255 * ta),))
        if cr is not None:
            ca = ease_out((tt - 0.9) / 0.6) * min(1, ot / 0.3)
            if ca > 0:
                c2 = cr.copy(); c2.putalpha(c2.getchannel("A").point(lambda v: int(v * ca)))
                im.alpha_composite(c2, ((W_ - cr.width) // 2, 880))
        w.stdin.write(im.convert("RGBA").tobytes())
    w.stdin.close(); w.wait()
    return W_, H_


KINDS = dict(circle=circle, stat=stat, kinetic=kinetic, name=name, chapter=chapter, title=title)


def render_one(spec, outdir, style):
    ext = "mp4" if spec["kind"] == "title" else "mov"
    out = os.path.join(outdir, f"{spec['id']}.{ext}")
    return spec["id"], KINDS[spec["kind"]](spec, out, style), out


def render_all(specs, outdir, style, workers=4, only=None):
    from concurrent.futures import ProcessPoolExecutor
    os.makedirs(outdir, exist_ok=True)
    todo = [s for s in specs if not only or s["id"] in only]
    sizes = {}
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(render_one, s, outdir, style) for s in todo]
        for f in futs:
            oid, size, out = f.result()
            sizes[oid] = size
            print("  rendered overlay", oid, size, flush=True)
    return sizes
