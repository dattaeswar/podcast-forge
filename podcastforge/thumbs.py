"""Thumbnails: YouTube (1280x720) and reel covers (1080x1920) with both people, a headline, topic photos in
circles, a red banner, the logo and an optional pre-rendered credit image. Zoned layout so text never sits on a face.
People are cut out of video frames with rembg:  pip install "podcast-forge[thumbs]" """
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import build as B, media, overlays as ov, sync as syncmod

RED = (226, 30, 36)


def cutouts(P, C):
    """Frame -> crop -> background removal -> work/thumb/cut_<speaker>.png"""
    try:
        from rembg import new_session, remove
    except ImportError:
        raise SystemExit('Thumbnail cut-outs need rembg:  pip install "podcast-forge[thumbs]"')
    sess = new_session("isnet-general-use")
    out = {}
    for sid, c in P.cfg["thumbnails"]["cutouts"].items():
        dst = P.work("thumb", f"cut_{sid}.png")
        out[sid] = dst
        if os.path.exists(dst):
            continue
        cam = P.cam(c["camera"])
        f = P.work("thumb", f"frame_{sid}.png")
        media.grab_frame(P.path(cam["file"]), syncmod.cam_time(C.fr.sync, cam["name"], c["time"]), f)
        im = Image.open(f).convert("RGB")
        x0, y0, x1, y1 = c.get("crop", [0, 0, 1, 1])
        im = im.crop((int(x0 * im.width), int(y0 * im.height), int(x1 * im.width), int(y1 * im.height)))
        remove(im, session=sess, alpha_matting=False).save(dst)
        print(f"  cut-out {sid} -> {dst}")
    return out


def load_cut(path, bottom=0.65):
    im = Image.open(path).convert("RGBA")
    im = im.crop(im.getbbox())
    return im.crop((0, 0, im.width, int(im.height * bottom)))


def glow(person, color, r=16):
    a = person.getchannel("A")
    halo = a.filter(ImageFilter.MaxFilter(2 * (r // 2) + 1)).filter(ImageFilter.GaussianBlur(r))
    pad = r * 3
    out = Image.new("RGBA", (person.width + 2 * pad, person.height + 2 * pad), (0, 0, 0, 0))
    g = Image.new("RGBA", out.size, tuple(color) + (0,)); ga = Image.new("L", out.size, 0); ga.paste(halo, (pad, pad))
    g.putalpha(ga.point(lambda v: min(255, int(v * 1.6))))
    out = Image.alpha_composite(out, g)
    rim = Image.new("RGBA", out.size, (255, 236, 200, 0)); ra = Image.new("L", out.size, 0)
    ra.paste(a.filter(ImageFilter.MaxFilter(5)), (pad, pad)); rim.putalpha(ra)
    out = Image.alpha_composite(out, rim)
    out.alpha_composite(person, (pad, pad))
    return out, pad


def background(w, h, img):
    bg = ImageOps.fit(Image.open(img).convert("RGB"), (w, h)).filter(ImageFilter.GaussianBlur(6))
    arr = np.asarray(ImageOps.grayscale(bg).convert("RGB")).astype(np.float32) / 255
    yy, xx = np.mgrid[0:h, 0:w]
    rad = np.clip(1 - np.hypot((xx - w * 0.35) / w, (yy - h * 0.45) / h) * 1.5, 0, 1)[..., None]
    tint = np.array([0.55, 0.12, 0.04]) * rad + np.array([0.05, 0.03, 0.03]) * (1 - rad)
    return Image.fromarray(np.clip((arr * 0.35 * (0.4 + rad) + tint * 0.9) * 255, 0, 255).astype(np.uint8)).convert("RGBA")


def circle_img(S, spec, d, ring=10):
    img = ImageOps.exif_transpose(Image.open(spec["image"])).convert("RGB")
    src = ov.framed(img, d, spec.get("focus", (0.5, 0.4)), spec.get("fit", 1.0))
    m = Image.new("L", (d * 3, d * 3), 0); ImageDraw.Draw(m).ellipse((0, 0, d * 3 - 1, d * 3 - 1), fill=255)
    m = m.resize((d, d), Image.LANCZOS)
    pad = ring + 18; out = Image.new("RGBA", (d + 2 * pad,) * 2, (0, 0, 0, 0))
    gl = Image.new("L", out.size, 0); ImageDraw.Draw(gl).ellipse((pad - ring, pad - ring, pad + d + ring, pad + d + ring), fill=230)
    gc = Image.new("RGBA", out.size, S.acc + (0,)); gc.putalpha(gl.filter(ImageFilter.GaussianBlur(12))); out = Image.alpha_composite(out, gc)
    ImageDraw.Draw(out).ellipse((pad - ring, pad - ring, pad + d + ring, pad + d + ring), fill=S.acc2)
    out.paste(src, (pad, pad), m)
    return out


def text(S, im, xy, s, size, fill, role="display", anchor="la", stroke=9):
    fnt = S.font(role, size, "Bold" if role == "title" else None)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    d.text((xy[0] + 6, xy[1] + 8), s, font=fnt, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170), anchor=anchor)
    layer = layer.filter(ImageFilter.GaussianBlur(5))
    ImageDraw.Draw(layer).text(xy, s, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0), anchor=anchor)
    im.alpha_composite(layer)
    return ImageDraw.Draw(im).textbbox(xy, s, font=fnt, anchor=anchor, stroke_width=stroke)


def banner(S, im, box, s, size, rot=-2.0):
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    f = S.font("display", size)
    tw = d.textlength(s, font=f)
    if tw + 50 > x1 - x0:                      # grow the bar to fit its text
        cx = (x0 + x1) / 2; x0, x1 = cx - tw / 2 - 28, cx + tw / 2 + 28
    d.rectangle((x0, y0, x1, y1), fill=RED + (255,))
    d.text(((x0 + x1) / 2, (y0 + y1) / 2), s, font=f, fill="white", anchor="mm")
    im.alpha_composite(layer.rotate(rot, resample=Image.BICUBIC, center=((x0 + x1) / 2, (y0 + y1) / 2)))


def paste_person(S, im, person, height, x, bottom):
    p = person.resize((int(person.width * height / person.height), height), Image.LANCZOS)
    g, pad = glow(p, S.acc)
    im.alpha_composite(g, (int(x - pad), int(bottom - p.height - pad)))


def stamp(P, S, im, logo_size, logo_xy, credit_w, credit_xy):
    T = P.cfg["thumbnails"]
    if P.cfg["logo"]["file"]:
        lg = Image.open(P.path(P.cfg["logo"]["file"])).convert("RGBA").resize((logo_size, logo_size), Image.LANCZOS)
        im.alpha_composite(lg, logo_xy)
    if T.get("credit_image"):
        c = Image.open(P.path(T["credit_image"])).convert("RGBA")
        c = c.resize((credit_w, int(c.height * credit_w / c.width)), Image.LANCZOS)
        x, y = credit_xy
        im.alpha_composite(c, (int(x), int(y if y >= 0 else im.height + y - c.height)))


def color(S, c):
    return {"white": (255, 255, 255), "accent": S.acc, "accent2": S.acc2}.get(c, tuple(c) if isinstance(c, (list, tuple)) else (255, 255, 255))


def build(P, C):
    T = P.cfg["thumbnails"]
    if not T:
        raise SystemExit("No `thumbnails:` section in project.yaml.")
    S = C.style
    cuts = cutouts(P, C)
    specs = {s["id"]: s for s in B.ov_specs(C)}
    outdir = P.out(T.get("out", "thumbnails"))
    os.makedirs(outdir, exist_ok=True)
    cam0 = P.cams[0]["name"]
    left = next(s["id"] for s in P.speakers if P.side(s["id"], cam0) == "L")
    right = next(s["id"] for s in P.speakers if P.side(s["id"], cam0) == "R")
    pb = {sid: c.get("bottom", 0.65) for sid, c in T["cutouts"].items()}
    # ---------------- YouTube 1280x720: headline band on top, faces in the bottom corners, topic photos between them
    W, H = 1280, 720
    for it in T.get("youtube", []):
        im = background(W, H, P.path(it["background"]))
        paste_person(S, im, load_cut(cuts[left], pb[left]), 480, -20, H + 10)
        paste_person(S, im, load_cut(cuts[right], pb[right]), 440, 865, H + 10)
        y = 14
        for h in it["headline"]:
            bb = text(S, im, (W / 2 - 40, y), h["text"], h.get("size", 100), color(S, h.get("color", "white")), h.get("font", "display"), "ma", 8)
            y = bb[3] + 2
        cs = it.get("circles", [])[:3]
        total = len(cs) * 256 + (len(cs) - 1) * 24
        for k, oid in enumerate(cs):
            im.alpha_composite(circle_img(S, specs[oid], 200), (int(W / 2 - 20 - total / 2 + k * 280), 300))
        if it.get("banner"):
            banner(S, im, (400, 632, 900, 700), it["banner"], 48, -1.5)
        stamp(P, S, im, 150, (W - 160, 8), 300, (14, -10))
        im.convert("RGB").save(f"{outdir}/{it['name']}.jpg", quality=95)
        print("  ", f"{outdir}/{it['name']}.jpg")
    # ---------------- reel covers 1080x1920 (key content in the central 4:5 band the Instagram grid shows)
    RW, RH = 1080, 1920
    for it in T.get("reels", []):
        im = background(RW, RH, P.path(T.get("reel_background") or it.get("background") or T["youtube"][0]["background"]))
        paste_person(S, im, load_cut(cuts[right], 0.72), 820, 200, RH + 20)
        paste_person(S, im, load_cut(cuts[left], 0.72), 900, -200, RH + 20)
        y = 300
        for k, ln in enumerate(it["lines"]):
            bb = text(S, im, (RW / 2, y), ln, 150 if k == 0 else 140, (255, 255, 255) if k == 0 else S.acc2, "display", "ma", 10)
            y = bb[3] + 6
        if it.get("sub"):
            banner(S, im, (60, y + 20, RW - 60, y + 120), it["sub"], 66, -2)
        if it.get("circle"):
            im.alpha_composite(circle_img(S, specs[it["circle"]], 250), (RW // 2 - 143, y + 150))
        stamp(P, S, im, 190, (RW - 210, 30), 700, (RW / 2 - 350, -40))
        im.convert("RGB").save(f"{outdir}/{it['name']}.jpg", quality=95)
        print("  ", f"{outdir}/{it['name']}.jpg")
