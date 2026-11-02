"""9:16 reels / Shorts cut from the trailer hooks (or any raw time ranges): vertical re-frame from the best camera,
hook title card, circles / stats / kinetic text re-laid for a vertical frame, music, logo above the app UI zone."""
import os

import numpy as np
from PIL import Image, ImageDraw

from . import audio, build, media, overlays as ov, render, speech as speechmod, sync as syncmod
from .plan import Planner

W, H = 1080, 1920


def _pieces(P, C, item):
    hooks = {h["id"]: h for h in P.cfg["trailer"]["hooks"]}
    pieces, hk = [], []
    for hid in item.get("hooks", []):
        if hid not in hooks:
            raise SystemExit(f"Reel '{item['name']}' uses unknown trailer hook '{hid}'. Hooks: {list(hooks)}")
        h = hooks[hid]
        pieces += speechmod.pieces_for(C.speech, [h["raw"]], P.cfg["trailer"]["long_pause"], 0.10, 0.15, False, P.fps)
        hk.append(h)
    if item.get("ranges"):
        pieces += speechmod.pieces_for(C.speech, item["ranges"], P.cfg["trailer"]["long_pause"], 0.10, 0.15, False, P.fps)
    if not pieces:
        raise SystemExit(f"Reel '{item['name']}' needs `hooks:` or `ranges:`.")
    return pieces, hk


def vcrop(C, cam, sid, t_cam, tight):
    fr = C.fr
    SW, SH = fr.size[cam]
    hx, hy = fr.head(cam, sid, t_cam)
    h = SH * (0.80 if tight else 1.0)
    w = h * 9 / 16
    cx = hx * SW + (0.07 if fr.P.side(sid, cam) == "L" else -0.07) * w
    x = np.clip(cx - w / 2, 0, SW - w); y = np.clip(hy * SH - 0.30 * h, 0, SH - h)
    return int(x) // 2 * 2, int(y) // 2 * 2, int(w) // 2 * 2, int(h) // 2 * 2


def title_png(C, l1, l2, path):
    S = C.style
    im = Image.new("RGBA", (W, 330), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f1, f2 = S.font("title", 64, "Bold"), S.font("title", 70, "Bold")
    w = max(d.textlength(l1, font=f1), d.textlength(l2, font=f2)) + 80
    x0 = (W - w) / 2
    d.rounded_rectangle((x0, 20, x0 + w, 270), 28, fill=(10, 10, 14, 215), outline=S.acc + (255,), width=3)
    d.text(((W - d.textlength(l1, font=f1)) / 2, 50), l1, font=f1, fill=(255, 255, 255, 255))
    d.text(((W - d.textlength(l2, font=f2)) / 2, 140), l2, font=f2, fill=S.acc2 + (255,))
    im.save(path)


def cta_png(C, text, path):
    S = C.style
    im = Image.new("RGBA", (W, 160), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f = S.font("title", 46, "Bold")
    tw = d.textlength(text, font=f)
    d.rounded_rectangle(((W - tw) / 2 - 34, 30, (W + tw) / 2 + 34, 120), 45, fill=S.acc + (240,))
    d.text(((W - tw) / 2, 48), text, font=f, fill=(15, 15, 15, 255))
    im.save(path)


def build_reel(C, item):
    P = C.P
    fr = C.fr
    rc = P.cfg["reels"]
    cam = rc["camera"] or max(P.cams, key=lambda c: fr.size[c["name"]][0])["name"]
    name = item["name"]
    d = P.work("reels", name)
    os.makedirs(d, exist_ok=True)
    pieces, hooks = _pieces(P, C, item)
    pl = Planner(P, fr, C.speech, C.spk)
    segs = pl.plan(pieces, 1.3, 3.2, 99)
    # ---- vertical segments: always centred on a person; alternate punched-in / wider so jump cuts stay hidden
    prev, vsegs = None, []
    for s in segs:
        sid = fr.subject(s["shot"]) or s["spk"]
        tight = fr.scale(s["shot"]) == 2 or bool(s.get("push"))
        if prev and prev == (sid, tight):
            tight = not tight
        vsegs.append(dict(s, sid=sid, tight=tight)); prev = (sid, tight)
    names = []
    for k, s in enumerate(vsegs):
        out = os.path.join(d, f"s{k:03d}_{int(s['i'] * 100)}_{s['frames']}_{int(s['tight'])}_{s['sid']}.mp4")
        names.append(os.path.basename(out))
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            continue
        t = syncmod.cam_time(fr.sync, cam, s["i"])
        x, y, w, h = vcrop(C, cam, s["sid"], t, s["tight"])
        vf = [f"crop={w}:{h}:{x}:{y}", f"lut3d=file='{media.fpath(C.luts[cam])}':interp=tetrahedral", f"scale={W}:{H}:flags=lanczos"]
        if s.get("push"):
            n, z = s["frames"], 0.035
            a, b = f"W*{z / 2}*in/{n}", f"H*{z / 2}*in/{n}"
            vf.append(f"perspective=x0='{a}':y0='{b}':x1='W-{a}':y1='{b}':x2='{a}':y2='H-{b}':x3='W-{a}':y3='H-{b}':interpolation=cubic:eval=frame")
        vf += ["unsharp=5:5:0.45", "vignette=angle=PI/5", f"fps={P.fps}"]
        e = P.cfg["encode"]
        cmd = ["ffmpeg", "-v", "error", "-y", *media.hwaccel(P), "-ss", f"{t:.4f}", "-i", P.path(P.cam(cam)["file"]), "-frames:v",
               str(s["frames"]), "-vf", ",".join(vf), "-an", "-c:v", "libx264", "-preset", e["preset"], "-crf", str(e["crf"]),
               "-pix_fmt", "yuv420p", "-r", str(P.fps), out]
        try:
            media.run(cmd)
        except RuntimeError:
            media.run([c for c in cmd if c not in ("-hwaccel", *media.hwaccel(P))])
    lst = os.path.join(d, "list.txt")
    open(lst, "w").writelines(f"file '{n}'\n" for n in names)
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", d + ".mp4"])
    vid = render.speed_video(P, d + ".mp4", d + "_fast.mp4")
    # ---- audio
    audio.assemble_voice(P, pieces, d + "_voice.wav")
    voice = audio.speed_audio(P, d + "_voice.wav", d + "_voice_fast.wav")
    f = audio.out_map(P, pieces)
    dur = sum(round((b - a) * P.fps) for a, b in pieces) / P.fps / P.speed
    music = []
    m = item.get("music") or next((h["music"] for h in hooks if h.get("music")), None)
    if m:
        music.append((P.path(m["file"]), 0, dur + 0.3, m.get("gain", -9), m.get("offset", 0)))
    # ---- overlays re-laid for 9:16
    specs = {s["id"]: s for s in build.ov_specs(C)}
    wanted = [(o["id"], o["at"]) for h in hooks for o in h.get("overlays", [])] + [(o["id"], o["at"]) for o in item.get("overlays", [])]
    vk = {}
    for oid, _ in wanted:
        if specs[oid]["kind"] == "kinetic":
            vk[oid] = dict(specs[oid], id=f"kv_{oid}", w=W, h=H, size=118, cy=1180)
    allspecs = list(specs.values()) + list(vk.values())
    paths = build.render_overlays(C, allspecs, only={o for o, _ in wanted if specs[o]["kind"] != "kinetic"} | {s["id"] for s in vk.values()})
    ovs, sfx = [], []
    for oid, raw in wanted:
        s, t = specs[oid], f(raw) / P.speed
        if s["kind"] == "kinetic":
            ovs.append((paths[f"kv_{oid}"], t, 0, 0))
        elif s["kind"] == "circle":
            ovs.append((paths[oid], t, 250, 1010)); sfx += build.sfx_for(P, s, t)
        elif s["kind"] == "stat":
            ovs.append((paths[oid], t, 30, 1430, None, 0.88))
    audio.mix(P, voice, music, sfx, d + "_mix.wav", duck_db=P.cfg["audio"]["trailer_duck_db"])
    title_png(C, item["line1"], item["line2"], d + "_title.png")
    extra = [(d + "_title.png", 0.0, 0, 120, dur)]
    if rc["cta"]:
        cta_png(C, rc["cta"], d + "_cta.png")
        extra.append((d + "_cta.png", max(0.0, dur - 2.8), 0, 1560, 2.8))
    lb = build.logo_badge(P, 150)
    logo = [(lb[0], 0.0, W - lb[1][0] - 20, H - lb[1][1] - 430, dur)] if lb else []
    out = P.out("reels", f"{name}.mp4")
    render.composite(P, vid, d + "_mix.wav", extra + ovs + logo, out, dur, k=1)
    print(f"  {name}: {dur:.1f}s, {len(segs)} shots -> {out}")
    return out


def build_all(C, only=None):
    items = C.P.cfg["reels"]["items"]
    if not items:
        raise SystemExit("No reels configured. Add `reels.items` to project.yaml (see examples/project.example.yaml).")
    for it in items:
        if not only or it["name"] in only:
            build_reel(C, it)
