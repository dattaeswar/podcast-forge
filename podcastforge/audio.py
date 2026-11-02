"""Voice chain, frame-exact voice assembly, music ducking and EBU R128 mastering."""
import json
import os
import subprocess

import numpy as np
import soundfile as sf

from . import media

SR = 48000

CLEAN_CHAIN = ("highpass=f=80,afftdn=nr={nr}:nf=-35,deesser=i=0.35,dynaudnorm=f=150:g=9:p=0.9:m=8,"
               "acompressor=threshold=-20dB:ratio=3:attack=8:release=140:makeup=2,alimiter=limit=0.89")


def _aligned(P, dst, sr, af=None):
    """Extract the audio source onto the MASTER timeline (the audio camera may be a different, synced file)."""
    from . import sync
    src = P.audio_cam
    off = 0.0
    if src["name"] != P.master["name"]:
        off = sync.load(P)[src["name"]]["offset"]          # t_src = t_master + off
    media.extract_audio(P.path(src["file"]), dst, sr, channels=1, ss=off if off > 0 else None, af=af)
    if off < 0:                                            # source starts before the master's t=0: pad with silence
        x, _ = sf.read(dst, dtype="float32")
        sf.write(dst, np.concatenate([np.zeros(int(-off * sr), np.float32), x]), sr, subtype="PCM_16" if sr <= 16000 else "PCM_24")
    return dst


def raw16(P):
    """Untouched 16 kHz mono audio on the master timeline: used for speech detection and speaker models."""
    return _aligned(P, P.work("audio", "voice16.wav"), 16000)


def master_voice(P):
    """Cleaned 48 kHz mono voice on the master timeline -> work/master_voice.wav."""
    af = CLEAN_CHAIN.format(nr=P.cfg["audio"]["denoise_db"]) if P.cfg["audio"]["clean"] else None
    return _aligned(P, P.work("master_voice.wav"), SR, af)


def assemble_voice(P, pieces, path, fade=0.006):
    x, sr = sf.read(P.work("master_voice.wav"), dtype="float32", always_2d=True)
    fps = P.fps
    parts, nf = [], int(fade * sr)
    ramp = np.linspace(0, 1, nf, dtype=np.float32)[:, None]
    for s, e in pieces:
        n = int(round((e - s) * fps)) * sr // fps
        seg = x[int(round(s * sr)):int(round(s * sr)) + n].copy()
        if len(seg) < n:
            seg = np.vstack([seg, np.zeros((n - len(seg), seg.shape[1]), np.float32)])
        seg[:nf] *= ramp; seg[-nf:] *= ramp[::-1]
        parts.append(seg)
    y = np.concatenate(parts)
    sf.write(path, np.repeat(y, 2, axis=1) if y.shape[1] == 1 else y, sr, subtype="PCM_24")
    return len(y) / sr


def out_map(P, pieces):
    """raw master time -> output (1.0x) time for a piece list."""
    fps = P.fps
    starts = np.cumsum([0] + [round((e - s) * fps) for s, e in pieces]) / fps

    def f(t):
        for (s, e), o in zip(pieces, starts):
            if s - 0.05 <= t <= e:
                return float(o + max(0.0, t - s))
        nxt = [o for (s, e), o in zip(pieces, starts) if s > t]
        return float(nxt[0] if nxt else starts[-1])
    return f


def speed_audio(P, src, dst):
    sp = P.speed
    if abs(sp - 1) < 1e-6:
        _copy(src, dst)
        return dst
    try:
        media.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", f"rubberband=tempo={sp}:pitchq=quality", "-c:a", "pcm_s24le", dst])
    except RuntimeError:                       # ffmpeg without librubberband: fall back to atempo
        media.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", f"atempo={sp}", "-c:a", "pcm_s24le", dst])
    return dst


def _copy(src, dst):
    x, sr = sf.read(src, dtype="float32", always_2d=True)
    sf.write(dst, x, sr, subtype="PCM_24")


def envelope(v, attack=0.03, release=0.45):
    e = np.abs(v).max(1)
    blk = SR // 100
    e = e[: len(e) // blk * blk].reshape(-1, blk).max(1)
    act = (e > 0.02).astype(np.float32)
    out = np.zeros_like(act); g = 0.0
    a, r = 1 - np.exp(-1 / (attack * 100)), 1 - np.exp(-1 / (release * 100))
    for i, x in enumerate(act):
        g += (a if x > g else r) * (x - g)
        out[i] = g
    return np.repeat(out, blk)


def mix(P, voice_path, music, sfx, out_wav, duck_db=-14.0, tail=0.0):
    """music: [(path, start_s, end_s, gain_db, offset_s)]   sfx: [(path, at_s, gain_db)]
    Music ducks under the voice; the result is two-pass loudness-normalised to the project's target."""
    v, _ = sf.read(voice_path, dtype="float32", always_2d=True)
    if v.shape[1] == 1:
        v = np.repeat(v, 2, 1)
    n = len(v) + int(tail * SR)
    v = np.vstack([v, np.zeros((n - len(v), 2), np.float32)])
    env = envelope(v)
    env = np.concatenate([env, np.zeros(max(0, n - len(env)), np.float32)])[:n]
    duck = 10 ** (duck_db * env / 20)
    bed = np.zeros((n, 2), np.float32)
    for path, a, b, g, off in music:
        a_i, b_i = int(a * SR), min(n, int(b * SR))
        if b_i <= a_i or not os.path.exists(path):
            continue
        m = media.load_f32(path, SR, 2, ss=off)
        m = np.tile(m, (int(np.ceil((b_i - a_i) / len(m))), 1))[: b_i - a_i]
        fi, fo = min(len(m), int(0.35 * SR)), min(len(m), int(0.6 * SR))
        m[:fi] *= np.linspace(0, 1, fi)[:, None]
        m[-fo:] *= np.linspace(1, 0, fo)[:, None]
        bed[a_i:b_i] += m * 10 ** (g / 20)
    bed *= duck[:, None]
    fx = np.zeros((n, 2), np.float32)
    for path, at, g in sfx:
        if not os.path.exists(path):
            continue
        s = media.load_f32(path, SR, 2)
        a_i = max(0, int(at * SR)); b_i = min(n, a_i + len(s))
        fx[a_i:b_i] += s[: b_i - a_i] * 10 ** (g / 20)
    y = v + bed + fx
    pre = out_wav + ".pre.wav"
    sf.write(pre, y, SR, subtype="FLOAT")
    return master(P, pre, out_wav, delete=True)


def master(P, src, dst, delete=False):
    """Two-pass EBU R128 loudness normalisation (linear, true-peak safe)."""
    I, TP = P.cfg["audio"]["loudness"], P.cfg["audio"]["true_peak"]
    m = subprocess.run(["ffmpeg", "-hide_banner", "-i", src, "-af", f"loudnorm=I={I}:TP={TP}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
    js = json.loads(m[m.rindex("{"):m.rindex("}") + 1])
    if float(js["input_i"]) < -69:                      # silence (e.g. a title card without music): nothing to normalise
        media.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-c:a", "pcm_s24le", dst])
        if delete:
            os.remove(src)
        return len(sf.read(dst, dtype="float32", always_2d=True)[0]) / SR
    ln = (f"loudnorm=I={I}:TP={TP}:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    media.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", ln + ",aresample=48000", "-c:a", "pcm_s24le", dst])
    if delete:
        os.remove(src)
    return len(sf.read(dst, dtype="float32", always_2d=True)[0]) / SR
