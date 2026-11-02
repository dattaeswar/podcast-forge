"""Word-level transcript (faster-whisper) used to place photos/stat cards on the words that are spoken.
Install the extra:  pip install "podcast-forge[transcribe]"  (downloads a Whisper model on first use)."""
import json
import os

import soundfile as sf

from . import audio, speech as speechmod

CHUNK = 600.0


def ranges_to_transcribe(P, dur):
    rs = [list(r) for r in speechmod.episode_ranges(P, dur)]
    for h in P.cfg["trailer"]["hooks"]:
        rs.append([max(0.0, h["raw"][0] - 2), h["raw"][1] + 2])
    rs.sort()
    merged = []
    for a, b in rs:
        if merged and a - merged[-1][1] < 4:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:                       # chunks keep memory flat and make progress visible
        t = a
        while t < b:
            out.append([t, min(b, t + CHUNK)]); t += CHUNK
    return out


def run(P, dur):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit('Transcription needs faster-whisper:  pip install "podcast-forge[transcribe]"')
    cfg = P.cfg["transcribe"]
    wav = audio.raw16(P)
    x, sr = sf.read(wav, dtype="float32")
    lang = None if cfg["language"] in (None, "auto") else cfg["language"]
    model = WhisperModel(cfg["model"], device=cfg["device"], compute_type="int8" if cfg["device"] == "cpu" else "float16")
    words, out = [], P.work("words.json")
    rs = ranges_to_transcribe(P, dur)
    for k, (a, b) in enumerate(rs):
        segs, _ = model.transcribe(x[int(a * sr):int(b * sr)], language=lang, word_timestamps=True, beam_size=1,
                                   condition_on_previous_text=False, vad_filter=True)
        for s in segs:
            for w in s.words:
                words.append([round(a + w.start, 2), round(a + w.end, 2), w.word.strip()])
        json.dump(words, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  transcribed {k + 1}/{len(rs)} chunks ({len(words)} words)", flush=True)
    return words


def search(P, pattern, context=6):
    import re
    f = P.work("words.json")
    if not os.path.exists(f):
        raise SystemExit("Run `pf transcribe` first.")
    words = json.load(open(f, encoding="utf-8"))
    text, idx = "", []
    for i, (s, e, w) in enumerate(words):
        idx.append((len(text), i)); text += w.lower() + " "
    hits = []
    for m in re.finditer(pattern, text, flags=re.I):
        i = max(j for p, j in idx if p <= m.start())
        ctx = " ".join(w for _, _, w in words[max(0, i - context):i + context])
        hits.append((words[i][0], ctx))
    for t, ctx in hits:
        print(f"{t:8.1f}s  ({int(t // 60):02d}:{int(t % 60):02d})  ...{ctx}...")
    if not hits:
        print("no match")
    return hits
