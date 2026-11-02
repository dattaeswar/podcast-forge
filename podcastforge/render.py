"""Video rendering: graded multicam segments -> concat -> speed -> overlay composite."""
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import media, sync as syncmod


def seg_cmd(P, fr, luts, s, out, hw=True):
    cam = fr.cam_of(s["shot"])
    t = syncmod.cam_time(fr.sync, cam, s["i"])
    x, y, w, h = fr.crop(s["shot"], t)
    W, H = P.res
    sharp = P.cam(cam).get("sharpen", 0.45)
    vf = [f"crop={w}:{h}:{x}:{y}", f"lut3d=file='{media.fpath(luts[cam])}':interp=tetrahedral" if luts.get(cam) else "null",
          f"scale={W}:{H}:flags=lanczos"]
    if s.get("push"):                       # slow sub-pixel push-in (perspective warp avoids zoompan's pixel jitter)
        n, z = s["frames"], 0.035
        a, b = f"W*{z / 2}*in/{n}", f"H*{z / 2}*in/{n}"
        vf.append(f"perspective=x0='{a}':y0='{b}':x1='W-{a}':y1='{b}':x2='{a}':y2='H-{b}':x3='W-{a}':y3='H-{b}'"
                  ":interpolation=cubic:eval=frame")
    vf += [f"unsharp=5:5:{sharp}", "vignette=angle=PI/5:mode=forward", f"fps={P.fps}"]
    e = P.cfg["encode"]
    return ["ffmpeg", "-v", "error", "-y", *(media.hwaccel(P) if hw else []), "-ss", f"{t:.4f}", "-i", P.path(P.cam(cam)["file"]),
            "-frames:v", str(s["frames"]), "-vf", ",".join(vf), "-an", "-c:v", "libx264", "-preset", e["preset"],
            "-crf", str(e["crf"]), "-pix_fmt", "yuv420p", "-r", str(P.fps), out]


def seg_name(P, luts, s, k):
    key = json.dumps([s["shot"], round(s["i"], 3), s["frames"], bool(s.get("push")), P.res, P.cfg["encode"],
                      {c: os.path.getmtime(p) for c, p in luts.items() if p and os.path.exists(p)}], sort_keys=True)
    return f"s{k:04d}_{hashlib.md5(key.encode()).hexdigest()[:8]}.mp4"


def render_segments(P, fr, luts, segs, outdir, workers=None, label=""):
    """Render every segment (cached by content hash), concat them to <outdir>.mp4 and return that path."""
    os.makedirs(outdir, exist_ok=True)
    workers = workers or max(1, min(4, (os.cpu_count() or 4) // 2))
    names = [seg_name(P, luts, s, k) for k, s in enumerate(segs)]
    jobs = [(s, os.path.join(outdir, nm)) for s, nm in zip(segs, names) if not (os.path.exists(os.path.join(outdir, nm))
                                                                               and os.path.getsize(os.path.join(outdir, nm)) > 1000)]

    def run(j):
        try:
            media.run(seg_cmd(P, fr, luts, *j))
        except RuntimeError:                       # hardware decode can fail on odd sources: retry in software
            media.run(seg_cmd(P, fr, luts, *j, hw=False))
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for _ in ex.map(run, jobs):
            done += 1
            if done % 25 == 0 or done == len(jobs):
                print(f"  {label} segments {done}/{len(jobs)}", flush=True)
    lst = os.path.join(outdir, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{nm}'\n" for nm in names)
    out = outdir.rstrip("/\\") + ".mp4"
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


def speed_video(P, src, dst):
    if abs(P.speed - 1) < 1e-6:
        return src
    if os.path.exists(dst) and os.path.getmtime(dst) > os.path.getmtime(src):
        return dst
    media.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"setpts=PTS/{P.speed},fps={P.fps}", "-an", "-c:v", "libx264",
               "-preset", P.cfg["encode"]["preset"], "-crf", "14", "-pix_fmt", "yuv420p", dst])
    return dst


def composite(P, video, audio, overlays, out, dur=None, k=None):
    """overlays: [(file, at_s, x, y[, dur][, scale])]. .mov/.mp4 files are alpha clips; .png are held for `dur` seconds.
    Positions are authored for a 1920-wide frame and scaled by `k` (default: output width / 1920)."""
    k = k if k is not None else P.res[0] / 1920
    args = ["ffmpeg", "-v", "error", "-y", "-i", video, "-i", audio]
    fc, last = [], "0:v"
    for i, o in enumerate(overlays):
        f, at, x, y = o[:4]
        if f.endswith(".png"):
            args += ["-itsoffset", f"{at:.3f}", "-loop", "1", "-t", f"{o[4]:.3f}", "-framerate", str(P.fps), "-i", f]
        else:
            args += ["-itsoffset", f"{at:.3f}", "-i", f]
        src = f"[{i + 2}:v]"
        sc = (o[5] if len(o) > 5 and o[5] else 1.0) * k
        if abs(sc - 1) > 1e-3:
            fc.append(f"{src}scale=iw*{sc:.4f}:-1[sc{i}]"); src = f"[sc{i}]"
        fc.append(f"[{last}]{src}overlay=x={int(x * k)}:y={int(y * k)}:eof_action=pass:format=auto[v{i}]")
        last = f"v{i}"
    args += (["-filter_complex", ";".join(fc), "-map", f"[{last}]"] if fc else ["-map", "0:v"])
    e = P.cfg["encode"]
    args += ["-map", "1:a", "-c:v", "libx264", "-preset", e["final_preset"], "-crf", str(e["final_crf"]), "-pix_fmt", "yuv420p",
             "-r", str(P.fps), "-profile:v", "high", "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]
    if dur:
        args += ["-t", f"{dur:.3f}"]
    media.run(args + [out])
    return out
