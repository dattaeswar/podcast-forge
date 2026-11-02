"""Colour: match every camera to one reference camera (Reinhard in Lab), then apply a shared look.
Output is one 33^3 .cube LUT per camera (work/lut/<camera>.cube) plus work/grade_preview.jpg."""
import os

import cv2
import numpy as np

from . import media, sync as syncmod

N = 33
PRESETS = {
    # black lift, exposure, warm highlights (r,g,b), S-curve contrast, saturation
    "studio_warm": dict(black=0.03, expo=0.97, warm=[0.045, 0.012, -0.05], contrast=0.20, sat=1.10),
    "neutral": dict(black=0.0, expo=1.0, warm=[0, 0, 0], contrast=0.08, sat=1.0),
    "cinematic": dict(black=0.04, expo=0.95, warm=[0.03, 0.0, -0.04], contrast=0.28, sat=1.05),
    "punchy": dict(black=0.03, expo=0.98, warm=[0.03, 0.01, -0.03], contrast=0.32, sat=1.25),
    "none": None,
}


def look(c, P):
    c = np.clip((c - P["black"]) / (1 - P["black"]), 0, 1) * P["expo"]
    L = (c * [0.2126, 0.7152, 0.0722]).sum(-1, keepdims=True)
    hi = np.clip(L, 0, 1) ** 0.8                                    # warm mids / highlights, cool shadows
    sh = (1 - np.clip(L, 0, 1)) ** 3
    c = c * (1 + hi * np.array(P["warm"])) + sh * np.array([-0.012, 0.004, 0.018])
    c = np.clip(c, 0, 1)
    c = c - P["contrast"] * np.sin(2 * np.pi * c) / (2 * np.pi)      # S-curve
    L = (c * [0.2126, 0.7152, 0.0722]).sum(-1, keepdims=True)
    sat = np.abs(c - L).max(-1, keepdims=True)
    c = L + (c - L) * (P["sat"] + 0.15 * (1 - np.clip(sat * 4, 0, 1)))  # vibrance: lift the dull colours more
    c = np.where(c > 0.88, 0.88 + (1 - np.exp(-(c - 0.88) * 5)) * 0.12 / (1 - np.exp(-0.6)), c)  # highlight roll-off
    return np.clip(c, 0, 1)


def lab_stats(img):
    lab = cv2.cvtColor(img.astype(np.float32), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    return lab.mean(0), lab.std(0)


def match_fn(src_img, dst_img, amt):
    ms, ss = lab_stats(src_img); md, sd = lab_stats(dst_img)

    def f(c):
        sh = c.shape
        lab = cv2.cvtColor(c.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_RGB2LAB).reshape(-1, 3)
        lab2 = (lab - ms) * (sd / (ss + 1e-6)) + md
        lab = lab + amt * (lab2 - lab)
        return np.clip(cv2.cvtColor(lab.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_LAB2RGB), 0, 1).reshape(sh)
    return f


def write_cube(path, fn):
    g = np.linspace(0, 1, N, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")                    # red varies fastest in .cube
    rgb = np.stack([r, gg, b], -1).reshape(-1, 3)
    out = fn(rgb)
    with open(path, "w") as f:
        f.write(f"LUT_3D_SIZE {N}\n")
        for v in out:
            f.write("%.6f %.6f %.6f\n" % tuple(v))


def _frames(P, cam, sync, times):
    path = P.path(cam["file"])
    imgs = []
    for k, t in enumerate(times):
        f = P.work("frames", f"cal_{cam['name']}_{k}.png")
        media.grab_frame(path, syncmod.cam_time(sync, cam["name"], t), f, scale="960:-1")
        imgs.append(cv2.resize(cv2.imread(f)[..., ::-1].astype(np.float32) / 255, (960, 540)))
    return np.mean(imgs, 0)


def build(P):
    g = P.cfg["grade"]
    sync = syncmod.load(P) if len(P.cams) > 1 else {P.cams[0]["name"]: {"offset": 0.0, "drift": 0.0}}
    dur = media.probe(P.path(P.master["file"]))["duration"]
    times = g["calibration_times"] or [dur * 0.25, dur * 0.75]
    avg = {c["name"]: _frames(P, c, sync, times) for c in P.cams}
    preset = g["preset"]
    if preset not in PRESETS:
        raise SystemExit(f"Unknown grade.preset '{preset}'. Options: {list(PRESETS)}")
    params = {**(PRESETS[preset] or {}), **g["params"]} if PRESETS[preset] else None
    ref = g["reference"] or max(P.cams, key=lambda c: media.probe(P.path(c["file"]))["width"])["name"]
    os.makedirs(P.work("lut"), exist_ok=True)
    luts, fns = {}, {}
    for c in P.cams:
        n = c["name"]
        if g["lut"].get(n):
            luts[n] = P.path(g["lut"][n])
            continue
        match = None if n == ref else match_fn(avg[n], avg[ref], g["match_amount"])

        def fn(x, match=match):
            x = match(x) if match else x
            return look(x, params) if params else x
        fns[n] = fn
        luts[n] = P.work("lut", f"{n}.cube")
        write_cube(luts[n], fn)
    # before / after preview
    rows = []
    for c in P.cams:
        n = c["name"]
        raw = avg[n]
        out = fns[n](raw.reshape(-1, 3)).reshape(raw.shape) if n in fns else raw
        pair = np.ascontiguousarray((np.clip(np.hstack([raw, out]), 0, 1)[..., ::-1] * 255).astype(np.uint8))
        cv2.putText(pair, f"{n} raw", (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
        cv2.putText(pair, f"{n} graded", (976, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
        rows.append(pair)
    cv2.imwrite(P.work("grade_preview.jpg"), np.vstack(rows))
    print(f"  reference camera: {ref}; preset: {preset}; preview: {P.work('grade_preview.jpg')}")
    return luts
