"""Who is speaking when (two speakers): seeded voice models, optionally fused with on-camera motion.

Writes work/spk.npy at 10 Hz: 0 = speakers[0], 1 = speakers[1].
Seeds: `speakers[i].seed: [[start, end], ...]` = stretches (>= 8 s ideal) where ONLY that person talks.
Without seeds, speaker 0 is seeded from the first speech run (hosts usually open the show) and speaker 1 from the
longest runs (guests usually talk longest). Always check the printed host share, and fix mistakes with
`diarize.overrides: [[start, end, speaker-id], ...]`.
"""
import json

import librosa
import numpy as np
import soundfile as sf
from sklearn.mixture import GaussianMixture

from . import audio, speech as speechmod

SR, HOP = 16000, 160


def _features(x):
    mf = librosa.feature.mfcc(y=x, sr=SR, n_mfcc=20, n_fft=400, hop_length=HOP)[1:]
    mf = np.vstack([mf, librosa.feature.delta(mf)])
    return (mf - mf.mean(1, keepdims=True)) / (mf.std(1, keepdims=True) + 1e-6)


def voice_llr(P, wav16, speech_runs):
    x, _ = sf.read(wav16, dtype="float32")
    n = len(x) // HOP
    db = 10 * np.log10((x[: n * HOP].reshape(n, HOP) ** 2).mean(1) + 1e-10)
    floor = np.percentile(db, 5)
    voiced = db > floor + P.cfg["speech"]["hi_db"]
    mf = _features(x)
    L = min(mf.shape[1], n)
    runs = [[int(a * 100), int(b * 100)] for a, b in speech_runs]

    def frames(spans):
        idx = np.concatenate([np.arange(int(a * 100), int(b * 100)) for a, b in spans])
        idx = idx[idx < L]
        return mf[:, idx[voiced[idx]]].T

    s0, s1 = P.speakers[0].get("seed"), P.speakers[1].get("seed")
    long_runs = sorted(runs, key=lambda r: r[0] - r[1])
    first = next((r for r in runs if r[1] - r[0] >= 300), runs[0])
    seed0 = [tuple(map(float, s)) for s in s0] if s0 else [(first[0] / 100, min(first[1], first[0] + 800) / 100)]
    seed1 = [tuple(map(float, s)) for s in s1] if s1 else [(a / 100, b / 100) for a, b in long_runs[: max(1, len(runs) // 6)] if b - a > 600]
    if not seed1:
        seed1 = [(a / 100, b / 100) for a, b in long_runs[:3]]
    wins = []
    for a, b in runs:
        for s_ in range(a, max(a + 1, b - 50), 50):
            e = min(s_ + 100, b)
            if e - s_ >= 40 and voiced[s_:min(e, L)].sum() >= 15:
                wins.append((s_ / 100, e / 100))
    if len(wins) < 20:
        raise SystemExit("Not enough speech to model two speakers (need a few minutes of conversation).")
    f0, f1 = P.cfg["diarize"].get("seed_fraction", [0.15, 0.40])
    for it in range(3):
        X0, X1 = frames(seed0), frames(seed1)
        if len(X0) < 100 or len(X1) < 100:
            raise SystemExit("A speaker seed is too short. Give `speakers[i].seed` ranges with >= 5 s of that person alone.")
        X1 = X1[np.random.RandomState(0).choice(len(X1), min(len(X1), 60000), replace=False)]
        g0 = GaussianMixture(16, covariance_type="diag", random_state=0, max_iter=200).fit(X0)
        g1 = GaussianMixture(16, covariance_type="diag", random_state=0, max_iter=200).fit(X1)
        llr = np.array([g0.score(w) - g1.score(w) if len(w := frames([win])) else 0.0 for win in wins])
        llr_s = np.convolve(llr, np.ones(3) / 3, mode="same")
        order = np.argsort(llr_s)
        seed0 = [wins[k] for k in order[-max(20, int(f0 * len(wins))):]]
        seed1 = [wins[k] for k in order[: int(f1 * len(wins))]]
        print(f"  voice model iteration {it}: speaker0 share of windows = {(llr_s > 0).mean():.2f}")
    return wins, llr_s, n


def run(P, speech_runs=None, motion=None):
    wav = audio.raw16(P)
    speech_runs = speech_runs or speechmod.load(P)
    wins, llr_s, n = voice_llr(P, wav, speech_runs)
    N = int(max(b for _, b in speech_runs) * 10) + 20
    t = np.arange(N) / 10
    w = np.array([[(a + b) / 2, v] for (a, b), v in zip(wins, llr_s)])
    llr = np.interp(t, w[:, 0], w[:, 1])
    za = (llr - np.median(llr)) / (np.percentile(llr, 75) - np.percentile(llr, 25) + 1e-6)
    score = za.copy()
    sp = np.zeros(N, bool)
    for a, b in speech_runs:
        sp[int(a * 10):int(b * 10)] = True
    if motion is not None:                                    # columns: master_time, motion0, motion1
        v = np.log(motion[:, 1] + 0.004) - np.log(motion[:, 2] + 0.004)
        v = np.convolve(v, np.ones(10) / 10, mode="same")
        vm = np.interp(t, motion[:, 0], v)
        zv = (vm - np.median(vm)) / (np.percentile(vm, 75) - np.percentile(vm, 25) + 1e-6)
        agree = float(np.mean(np.sign(za[sp]) == np.sign(zv[sp])))
        print(f"  audio/visual agreement on speech: {agree:.2f}")
        if agree > 0.55:
            score = za + 0.8 * zv
        else:
            print("  (motion cue disagrees with the voice model - ignoring it)")
    lab = (np.convolve(score, np.ones(8) / 8, mode="same") <= 0.35).astype(np.int8)   # 0 = speaker 0
    for a, b, sid in P.cfg["diarize"]["overrides"]:
        lab[int(a * 10):int(b * 10)] = P.spk_index(sid)
    np.save(P.work("spk.npy"), lab)
    share = 1 - lab[sp].mean()
    print(f"  {P.speakers[0]['id']} speaks {share:.0%} of the speech, {P.speakers[1]['id']} {1 - share:.0%}")
    return lab


def motion_energy(P, fr, cam_name=None, W=640, H=360, FPS=10):
    """Per-speaker head motion on one camera (needs head positions) -> array [master_time, m0, m1]."""
    import subprocess
    from . import media, sync as syncmod
    cam = P.cam(cam_name or P.cfg["diarize"]["motion_camera"] or P.cams[0]["name"])
    p = subprocess.Popen(["ffmpeg", "-v", "error", *media.hwaccel(P), "-i", P.path(cam["file"]), "-vf",
                          f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
    prev, rows, i = None, [], 0
    while True:
        b = p.stdout.read(W * H)
        if len(b) < W * H:
            break
        g = np.frombuffer(b, np.uint8).astype(np.int16).reshape(H, W)
        t = i / FPS
        if prev is not None:
            d = np.abs(g - prev)
            r = [t]
            for s in P.speakers:
                hx, hy = fr.head(cam["name"], s["id"], t)
                cx, cy = int(hx * W), int(hy * H) + 8                  # bias toward mouth / jaw
                box = d[max(0, cy - 28):cy + 34, max(0, cx - 30):cx + 30]
                r.append(float((box > 12).mean()))
            rows.append(r)
        prev, i = g, i + 1
    m = np.array(rows, np.float32)
    c = fr.sync[cam["name"]]
    m[:, 0] = (m[:, 0] - c["offset"]) / (1 + c["drift"])                # camera time -> master time
    np.save(P.work("motion.npy"), m)
    return m
