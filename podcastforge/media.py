"""ffmpeg / ffprobe helpers shared by every stage."""
import json
import shutil
import subprocess
import sys

import numpy as np


def need(tool):
    if not shutil.which(tool):
        raise SystemExit(f"`{tool}` not found on PATH. Install FFmpeg (full build) from https://ffmpeg.org/download.html")


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:300]}\n{r.stderr[-1500:]}")
    return r


def probe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    out = {"duration": float(j["format"].get("duration", 0)), "has_audio": a is not None}
    if v:
        n, d = (v.get("avg_frame_rate") or "0/1").split("/")
        out.update(width=int(v["width"]), height=int(v["height"]), fps=float(n) / float(d or 1) if float(d or 1) else 0.0)
        rot = int(float((v.get("tags") or {}).get("rotate", 0)))
        for sd in v.get("side_data_list", []) or []:
            rot = int(sd.get("rotation", rot))
        if abs(rot) % 180 == 90:
            out["width"], out["height"] = out["height"], out["width"]
    return out


def hwaccel(project):
    h = project.cfg["hwaccel"]
    if h == "auto":
        h = {"win32": "d3d11va", "darwin": "videotoolbox"}.get(sys.platform, "none")
    return [] if h in ("none", None) else ["-hwaccel", h]


def extract_audio(src, dst, sr=16000, channels=1, ss=None, t=None, af=None):
    args = ["ffmpeg", "-v", "error", "-y"]
    if ss is not None:
        args += ["-ss", f"{ss:.3f}"]
    args += ["-i", src]
    if t is not None:
        args += ["-t", f"{t:.3f}"]
    args += ["-vn", "-ac", str(channels), "-ar", str(sr)]
    if af:
        args += ["-af", af]
    run(args + ["-c:a", "pcm_s16le" if sr <= 16000 else "pcm_s24le", dst])
    return dst


def load_f32(path, sr=48000, channels=2, ss=0.0, t=None):
    """Decode any audio/video file to a float32 numpy array (n, channels)."""
    args = ["ffmpeg", "-v", "error", "-ss", str(ss), "-i", path]
    if t:
        args += ["-t", str(t)]
    p = subprocess.run(args + ["-vn", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.float32).reshape(-1, channels).copy()


def fpath(p):
    """Escape a file path for use inside an ffmpeg filter argument (Windows drive colons, backslashes)."""
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def grab_frame(src, t, dst, scale=None):
    vf = ["-vf", f"scale={scale}"] if scale else []
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", *vf, "-q:v", "2", dst])
    return dst
