"""Multi-camera sync: GCC-PHAT cross-correlation of each camera's scratch audio against the master's.

Convention everywhere in the package:  t_cam = t_master * (1 + drift) + offset
"""
import json

import numpy as np
import soundfile as sf

from . import media

SR = 16000


def phat_lag(a, b):
    """Lag (s) such that a[t] ~ b[t + lag], plus a peak-sharpness score (>3 is a trustworthy match)."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    A, B = np.fft.rfft(a, n), np.fft.rfft(b, n)
    X = B * np.conj(A)
    c = np.fft.irfft(X / (np.abs(X) + 1e-9), n)
    k = int(np.argmax(c))
    lag = k if k < n // 2 else k - n
    return lag / SR, float(c[k] / (np.sort(c)[-50:-1].mean() + 1e-9))


def offset_at(master, other, t0, dur, search):
    a = master[int(t0 * SR):int((t0 + dur) * SR)]
    s0 = max(0.0, t0 - search)
    b = other[int(s0 * SR):int((t0 + dur + search) * SR)]
    lag, pk = phat_lag(a, b)
    return s0 + lag - t0, pk


def sync_cameras(P, points=5, window=120.0, search=40.0):
    """Measure every non-master camera against the master and write work/sync.json."""
    m = P.master
    mp = P.path(m["file"])
    mdur = media.probe(mp)["duration"]
    mwav = media.extract_audio(mp, P.work("audio", f"{m['name']}16.wav"), SR)
    master, _ = sf.read(mwav, dtype="float32")
    result = {"master": m["name"], "cameras": {m["name"]: {"offset": 0.0, "drift": 0.0}}}
    for c in P.cams:
        if c is m:
            continue
        cp = P.path(c["file"])
        info = media.probe(cp)
        if not info["has_audio"]:
            raise SystemExit(f"Camera '{c['name']}' has no audio track - needed to sync it. Add a clap or scratch audio.")
        wav = media.extract_audio(cp, P.work("audio", f"{c['name']}16.wav"), SR)
        other, _ = sf.read(wav, dtype="float32")
        win = min(window, mdur * 0.5)
        ts = np.linspace(0, max(0.0, mdur - win - 1), points)
        pts = [(float(t), *offset_at(master, other, float(t), win, search)) for t in ts]
        good = [p for p in pts if p[2] > 3]
        if not good:
            raise SystemExit(f"Could not sync camera '{c['name']}': no confident audio match. Both cameras must hear the "
                             f"same room. Try a clap at the start, or set `sync: {{offset: <seconds>}}` on the camera.")
        tt = np.array([p[0] for p in good]); oo = np.array([p[1] for p in good])
        drift, base = np.polyfit(tt, oo, 1) if len(good) > 2 else (0.0, float(np.median(oo)))
        if abs(drift) < 2e-5:                       # sub-frame over an hour: not real drift, keep a constant offset
            drift, base = 0.0, float(np.median(oo))
        if c.get("sync", {}).get("offset") is not None:
            base, drift = float(c["sync"]["offset"]), 0.0
        result["cameras"][c["name"]] = {"offset": float(base), "drift": float(drift), "points": pts}
        print(f"  {c['name']}: offset {base:+.3f}s  drift {drift:.1e}  ({len(good)}/{len(pts)} confident points)")
    json.dump(result, open(P.work("sync.json"), "w"), indent=1)
    return result


def load(P):
    try:
        return json.load(open(P.work("sync.json")))["cameras"]
    except FileNotFoundError:
        raise SystemExit("Run `pf sync` first.")


def cam_time(sync, cam, t):
    c = sync[cam]
    return t * (1 + c["drift"]) + c["offset"]
