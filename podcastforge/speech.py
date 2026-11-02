"""Speech activity detection and the silence / filler trimmer that turns raw ranges into tight 'pieces'."""
import json

import numpy as np
import soundfile as sf

HOP = 160  # 10 ms at 16 kHz


def detect(wav16, hi_db=19, lo_db=13):
    """Hysteresis speech gate relative to the recording's own noise floor. Returns (runs[[start_s, end_s]], floor_db)."""
    x, _ = sf.read(wav16, dtype="float32")
    n = len(x) // HOP
    db = 10 * np.log10((x[: n * HOP].reshape(n, HOP) ** 2).mean(1) + 1e-10)
    floor = float(np.percentile(db, 5))
    hi, lo = floor + hi_db, floor + lo_db
    on, st, segs = False, 0, []
    for i, v in enumerate(db):
        if not on and v > hi:
            j = i
            while j > 0 and db[j - 1] > lo and i - j < 30:   # back up to where the energy first rose
                j -= 1
            on, st = True, j
        elif on and v < lo:
            k = i
            while k < n and k - i < 12 and db[k] < lo:        # 120 ms hang time
                k += 1
            if k - i >= 12 or k == n:
                segs.append([st, i]); on = False
    if on:
        segs.append([st, n])
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < 15:
            merged[-1][1] = b
        elif b - a >= 10:
            merged.append([a, b])
    return [[a / 100, b / 100] for a, b in merged], floor, db


def save(P, runs, floor):
    json.dump({"speech": runs, "floor_db": floor}, open(P.work("speech.json"), "w"))


def load(P):
    try:
        return json.load(open(P.work("speech.json")))["speech"]
    except FileNotFoundError:
        raise SystemExit("Run `pf speech` first.")


def quantise(t, fps):
    return round(t * fps) / fps


def pieces_for(speech, ranges, long_pause, pre, post, drop_fillers, fps):
    """Tight phrases inside `ranges`: pauses longer than `long_pause` are removed, isolated short bursts
    (fillers, breaths, false starts) dropped, every phrase padded by pre/post and snapped to the frame grid."""
    out = []
    for a, b in ranges:
        sp = [(max(s, a), min(e, b)) for s, e in speech if e > a and s < b]
        if drop_fillers:
            sp = [(s, e) for i, (s, e) in enumerate(sp)
                  if not (e - s < 0.45 and (i == 0 or s - sp[i - 1][1] > 0.6) and (i == len(sp) - 1 or sp[i + 1][0] - e > 0.6))]
        cur = None
        for s, e in sp:
            if cur and s - cur[1] <= long_pause:
                cur[1] = e
            else:
                if cur:
                    out.append(cur)
                cur = [s, e]
        if cur:
            out.append(cur)
    res = []
    for s, e in out:
        s, e = quantise(s - pre, fps), quantise(e + post, fps)
        if res and s <= res[-1][1]:
            res[-1][1] = max(res[-1][1], e)
        elif e - s >= 0.3:
            res.append([s, e])
    return res


def episode_ranges(P, duration):
    """Raw master-time ranges that make the episode body: [start, end] minus the configured cuts."""
    e = P.cfg["edit"]
    a, b = float(e["start"] or 0.0), float(e["end"] or duration)
    cuts = sorted([list(map(float, c)) for c in e["cuts"]])
    rngs, cur = [], a
    for c0, c1 in cuts:
        if c0 > cur:
            rngs.append([cur, min(c0, b)])
        cur = max(cur, c1)
    if cur < b:
        rngs.append([cur, b])
    return [r for r in rngs if r[1] - r[0] > 0.3]
