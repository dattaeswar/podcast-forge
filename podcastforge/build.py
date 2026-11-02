"""Assembly: edit decision lists -> layout lock -> segments -> overlays + music -> trailer / episode / master."""
import hashlib
import json
import os
import re
import subprocess

import numpy as np
import soundfile as sf

from . import audio, media, overlays as ov, render, speech as speechmod, sync as syncmod
from .config import ConfigError
from .framing import Framing
from .plan import Planner
from .timeline_xml import xmeml

# circle x-position (1920-wide frame) for a given subject side: the circle sits on the side opposite the speaker
XPOS = {"L": 1290, "R": 50, None: 670}
CIRCLE_Y = 165


class Ctx:
    """Everything the later stages need, loaded once."""

    def __init__(self, P):
        P.check_files()
        self.P = P
        self.fr = Framing(P)
        self.speech = speechmod.load(P)
        self.spk = np.load(P.work("spk.npy")) if os.path.exists(P.work("spk.npy")) else None
        self.style = ov.Style.from_project(P)
        self.dur = media.probe(P.path(P.master["file"]))["duration"]
        self.luts = {}
        for c in P.cams:
            p = P.path(P.cfg["grade"]["lut"][c["name"]]) if P.cfg["grade"]["lut"].get(c["name"]) else P.work("lut", f"{c['name']}.cube")
            if not os.path.exists(p):
                raise SystemExit("Run `pf grade` first (no LUT found for camera '%s')." % c["name"])
            self.luts[c["name"]] = p
        wf = P.work("words.json")
        self.words = json.load(open(wf, encoding="utf-8")) if os.path.exists(wf) else []


# ---------------------------------------------------------------------- topic search
def find_time(words, rx, which="first", lo=0.0, hi=1e9):
    toks = [(s, w.lower()) for s, e, w in words if lo <= s <= hi]
    text, idx = "", []
    for s, w in toks:
        idx.append((len(text), s)); text += w + " "
    ms = [m.start() for m in re.finditer(rx, text, flags=re.I)]
    if not ms:
        return None
    pos = ms[0] if which == "first" else ms[-1] if which == "last" else ms[min(len(ms), int(which)) - 1]
    return max(s for p, s in idx if p <= pos)


def resolve_when(spec, words, lo, hi):
    w = spec.get("when") or {}
    if w.get("at") is not None:
        t = float(w["at"])
        return t if lo <= t <= hi else None
    if w.get("keywords"):
        a, b = (w.get("within") or [lo, hi])
        return find_time(words, w["keywords"], w.get("occurrence", "first"), max(lo, a), min(hi, b))
    return None


# ---------------------------------------------------------------------- overlays
def ov_specs(C, extra=()):
    P = C.P
    specs = []
    for o in list(P.cfg["overlays"]) + list(extra):
        s = dict(o)
        for k in ("image", "video", "logo", "credit"):
            if s.get(k):
                s[k] = P.path(s[k])
        if s["kind"] == "stat" and "count" in s and s["count"] is not None:
            s["count"] = list(s["count"]) + [""] * (4 - len(s["count"]))
        specs.append(s)
    return specs


def render_overlays(C, specs, only=None):
    """Render (or reuse cached) overlay clips into work/ov/. Returns {id: path}."""
    P = C.P
    d = P.work("ov")
    os.makedirs(d, exist_ok=True)
    todo, paths = [], {}
    for s in specs:
        if only and s["id"] not in only:
            continue
        ext = "mp4" if s["kind"] == "title" else "mov"
        out = os.path.join(d, f"{s['id']}.{ext}")
        h = hashlib.md5(json.dumps(s, sort_keys=True, default=str).encode()).hexdigest()
        hf = out + ".hash"
        paths[s["id"]] = out
        if not (os.path.exists(out) and os.path.exists(hf) and open(hf).read() == h):
            todo.append(s)
        s["_hash"] = h
    if todo:
        ov.render_all(todo, d, C.style, workers=max(1, min(4, (os.cpu_count() or 4) // 2)))
        for s in todo:
            open(paths[s["id"]] + ".hash", "w").write(s["_hash"])
    return paths


def place(spec, t_fast, x=None):
    """-> (file-less) (at, x, y) for an overlay in the 1920 x 1080 frame"""
    k = spec["kind"]
    if k == "circle":
        return t_fast, x, CIRCLE_Y
    return t_fast, *{"stat": (60, 790), "kinetic": (0, 0), "name": (60, 830), "chapter": (40, 40)}[k]


def sfx_for(P, spec, at):
    click = P.cfg["sfx"]["photo_click"]
    if click and spec["kind"] == "circle" and "video" not in spec:
        return [(P.path(click), at, -19)]
    return []


# ---------------------------------------------------------------------- circle layout lock
def lock_layout(C, segs, circles):
    """For each circle window pick the side that is free for most of it and swap any shot that would put the speaker
    under the circle for a compatible framing of the same person (or a wide). Returns x per circle."""
    fr = C.fr
    starts = np.cumsum([0] + [s["frames"] / C.P.fps for s in segs])
    xs, changed = [], 0
    for t, d in circles:
        idx = [k for k in range(len(segs)) if starts[k] < t + d and starts[k + 1] > t]
        cover = {}
        for k in idx:
            x = XPOS[fr.side(segs[k]["shot"])]
            cover[x] = cover.get(x, 0) + min(starts[k + 1], t + d) - max(starts[k], t)
        x = max(cover, key=cover.get) if cover else XPOS[None]
        for k in idx:
            if XPOS[fr.side(segs[k]["shot"])] == x:
                continue
            sid = fr.subject(segs[k]["shot"])
            nb = {segs[j]["shot"] for j in (k - 1, k + 1) if 0 <= j < len(segs)}
            # prefer another framing of the same person, then a cutaway to the other person, then a wide
            cands = [s for s in fr.shots if XPOS[fr.side(s)] == x]
            cands.sort(key=lambda s: (0 if fr.subject(s) == sid else 1 if fr.subject(s) else 2, fr.scale(s)))
            pick = next((c for c in cands if c not in nb), cands[0] if cands else None)
            if pick:
                segs[k]["shot"], segs[k]["push"] = pick, False
                changed += 1
        xs.append(x)
    return xs, changed


# ---------------------------------------------------------------------- edit decision lists
def build_edl(C):
    P = C.P
    if C.spk is None:
        raise SystemExit("Run `pf speakers` first.")
    e = P.cfg["edit"]
    pl = Planner(P, C.fr, C.speech, C.spk)
    res = {}
    # ---- trailer (cold open) from hand-picked hooks
    hooks = [dict(h) for h in P.cfg["trailer"]["hooks"]]
    if hooks:
        t = P.cfg["trailer"]
        tp = []
        for h in hooks:
            h["pieces"] = speechmod.pieces_for(C.speech, [h["raw"]], t["long_pause"], 0.10, 0.15, False, P.fps)
            tp += h["pieces"]
        punch = [tuple(h["raw"]) for h in hooks if h.get("punch")]
        segs = pl.plan(tp, t["min_shot"], t["max_shot"], 99, punch)
        res["trailer"] = {"pieces": tp, "segs": segs, "hooks": hooks}
    # ---- episode body
    ranges = speechmod.episode_ranges(P, C.dur)
    ep = speechmod.pieces_for(C.speech, ranges, e["long_pause"], e["pre"], e["post"], e["drop_fillers"], P.fps)
    esegs = pl.plan(ep, e["min_shot"], e["max_shot"], e["reaction_after"])
    assert sum(s["frames"] for s in esegs) == sum(int(round((b - a) * P.fps)) for a, b in ep), "video/audio frame mismatch"
    res["episode"] = {"pieces": ep, "segs": esegs}
    for k, v in res.items():
        json.dump(v, open(P.work(f"edl_{k}.json"), "w"), indent=0)
    cams = {c["name"]: P.path(c["file"]) for c in P.cams}
    for k, v in res.items():
        xmeml(P, C.fr, v["segs"], P.work(f"voice_{k}.wav"), P.out(f"timeline_{k}.xml"), k.title(), cams)
    kraw = sum(b - a for a, b in ranges)
    n = len(esegs)
    print(f"  episode: {kraw / 60:.1f} min raw -> {sum(b - a for a, b in ep) / 60:.1f} min tightened "
          f"({sum(b - a for a, b in ep) / P.speed / 60:.1f} min at {P.speed}x); {len(ep)} phrases, {n} shots, "
          f"{sum(s['push'] for s in esegs)} push-ins, {sum(bool(s.get('reaction')) for s in esegs)} reactions")
    return res


def load_edl(P, which):
    f = P.work(f"edl_{which}.json")
    if not os.path.exists(f):
        raise SystemExit("Run `pf edl` first.")
    return json.load(open(f))


# ---------------------------------------------------------------------- logo
def logo_badge(P, size=None):
    from PIL import Image, ImageFilter
    lf = P.cfg["logo"]["file"]
    if not lf:
        return None
    size = size or P.cfg["logo"]["size"]
    lg = Image.open(P.path(lf)).convert("RGBA").resize((size, size), Image.LANCZOS)
    pad = 16
    canvas = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 150), (pad + 3, pad + 6), lg.getchannel("A"))
    canvas = Image.alpha_composite(canvas, sh.filter(ImageFilter.GaussianBlur(7)))
    canvas.alpha_composite(lg, (pad, pad))
    out = P.work(f"logo_{size}.png")
    canvas.save(out)
    return out, canvas.size


def logo_overlay(P, dur, W=1920, H=1080, margin=(22, 18), lift=0, size=None):
    b = logo_badge(P, size)
    if not b:
        return []
    path, (w, h) = b
    return [(path, 0.0, W - w - margin[0], H - h - margin[1] - lift, dur)]


# ---------------------------------------------------------------------- common timeline helpers
def timed_events(C, pieces, specs_by_id, words, lo, hi, add_names=True):
    """Everything that should appear in the episode, with raw times: configured overlays, speaker name tags, chapter tags."""
    P = C.P
    f = audio.out_map(P, pieces)
    events = []
    for s in P.cfg["overlays"]:
        t = resolve_when(s, words, lo, hi)
        if t is not None:
            events.append((t, s["id"]))
        elif s.get("when"):
            print(f"  (overlay '{s['id']}' not placed: no match in {lo:.0f}-{hi:.0f}s)")
    chapters = []
    for i, ch in enumerate(P.cfg["chapters"]):
        t = resolve_when({"when": ch.get("when") or {"at": ch.get("at")}}, words, lo, hi)
        t = pieces[0][0] if i == 0 and t is None else t
        if t is None:
            print(f"  (chapter '{ch['title']}' not placed)")
            continue
        chapters.append((f"ch{i + 1}", t, ch["title"]))
        events.append((t + (3 if i == 0 else 0), f"ch{i + 1}"))
    if add_names and P.cfg.get("lower_thirds", True) and C.spk is not None:
        sp0 = P.speakers[0]
        first1 = next((a for a, b in pieces if a > 18 and C.spk[int(a * 10):int(b * 10)].mean() > 0.5), None)
        events.append((pieces[0][0] + 0.5, "n_" + sp0["id"]))
        if first1 is not None:
            events.append((first1 + 0.8, "n_" + P.speakers[1]["id"]))
    events.sort()
    timed, last_end = [], {}
    for raw, oid in events:
        spec = specs_by_id[oid]
        t = f(raw)
        lane = "c" if spec["kind"] == "circle" else spec["kind"]
        if t < last_end.get(lane, -1):                   # keep same-type overlays from stacking
            t = last_end[lane] + 0.5
        last_end[lane] = t + spec.get("dur", 4.5) + 0.8
        timed.append((t, raw, oid))
    return timed, chapters, f


def extra_specs(P):
    """Auto-generated overlays: speaker name tags and chapter tags."""
    out = []
    for s in P.speakers:
        out.append(dict(id="n_" + s["id"], kind="name", name=s.get("name", s["id"]), role=s.get("role", ""), dur=5.0))
    for i, ch in enumerate(P.cfg["chapters"]):
        out.append(dict(id=f"ch{i + 1}", kind="chapter", label=f"CHAPTER {i + 1}", title=ch["title"], dur=4.0))
    return out


def _music(P, path, a, b, gain, off=0.0):
    return (P.path(path), a, b, gain, off)


# ---------------------------------------------------------------------- episode
def build_episode(C):
    P = C.P
    E = load_edl(P, "episode")
    pieces, segs = E["pieces"], E["segs"]
    fps, sp = P.fps, P.speed
    dur = sum(round((b - a) * fps) for a, b in pieces) / fps / sp
    specs = ov_specs(C, extra_specs(P))
    by_id = {s["id"]: s for s in specs}
    timed, chapters, f = timed_events(C, pieces, by_id, C.words, pieces[0][0], pieces[-1][1])
    xs, changed = lock_layout(C, segs, [(t, by_id[o].get("dur", 4.5)) for t, r, o in timed if by_id[o]["kind"] == "circle"])
    if changed:
        print(f"  layout lock: {changed} shots reframed so circles never cover a face")
    used = {o for _, _, o in timed}
    paths = render_overlays(C, specs, only=used)
    xi = iter(xs)
    ovs, sfx, T = [], [], {}
    for t, raw, oid in timed:
        s = by_id[oid]
        at, x, y = place(s, t / sp, next(xi) if s["kind"] == "circle" else None)
        ovs.append((paths[oid], at, x, y)); sfx += sfx_for(P, s, at); T[oid] = at
        print(f"  {oid:16s} raw {raw:8.1f}s  ->  {at:7.1f}s")
    cue = lambda a: (f(float(a)) / sp if isinstance(a, (int, float)) else T.get(a) if a in T else
                     T.get(f"ch{int(a.split(':')[1])}") if str(a).startswith("chapter:") else None)
    m = P.cfg["music"]
    music = []
    if m["bed"]:
        b = m["bed"]
        music.append(_music(P, b["file"], 0, b.get("length", 15), b.get("gain", -15), b.get("offset", 0)))
    for c in m["cues"]:
        t0 = cue(c["at"])
        if t0 is not None:
            music.append(_music(P, c["file"], t0 + c.get("shift", 0), t0 + c.get("shift", 0) + c.get("length", 14), c.get("gain", -17), c.get("offset", 0)))
    if m["outro"]:
        o = m["outro"]
        music.append(_music(P, o["file"], dur - o.get("length", 16.5), dur + 1.5, o.get("gain", -14), o.get("offset", 0)))
    audio.assemble_voice(P, pieces, P.work("voice_episode.wav"))
    voice = audio.speed_audio(P, P.work("voice_episode.wav"), P.work("voice_episode_fast.wav"))
    audio.mix(P, voice, music, sfx, P.work("mix_episode.wav"), duck_db=P.cfg["audio"]["music_duck_db"], tail=1.5 if m["outro"] else 0)
    vid = render.render_segments(P, C.fr, C.luts, segs, P.work("seg_episode"), label="episode")
    vid = render.speed_video(P, vid, P.work("seg_episode_fast.mp4"))
    render.composite(P, vid, P.work("mix_episode.wav"), ovs + logo_overlay(P, dur), P.work("part_episode.mp4"), dur)
    json.dump({"chapters": [(c, f(raw) / sp, name) for c, raw, name in chapters]}, open(P.work("episode_chapters.json"), "w"))
    print(f"  episode part: {dur / 60:.1f} min")
    return dur


# ---------------------------------------------------------------------- trailer + title card
def build_trailer(C):
    P = C.P
    E = load_edl(P, "trailer")
    pieces, segs, hooks = E["pieces"], E["segs"], E["hooks"]
    fps, sp = P.fps, P.speed
    f = audio.out_map(P, pieces)
    dur = sum(round((b - a) * fps) for a, b in pieces) / fps / sp
    specs = ov_specs(C)
    by_id = {s["id"]: s for s in specs}
    circ = [(f(o["at"]), by_id[o["id"]].get("dur", 4.5)) for h in hooks for o in h.get("overlays", []) if by_id[o["id"]]["kind"] == "circle"]
    xs, changed = lock_layout(C, segs, circ)
    paths = render_overlays(C, specs, only={o["id"] for h in hooks for o in h.get("overlays", [])})
    xi = iter(xs)
    ovs, music, sfx, cur = [], [], [], None
    for h in hooks:
        h0 = f(h["pieces"][0][0]) / sp
        h1 = f(h["pieces"][-1][1] - 0.01) / sp
        if h.get("music") is not None or h is hooks[0]:
            if cur:
                music.append((cur[0], cur[1], h0 + 0.15, cur[3], cur[2]))
            m = h.get("music")
            cur = (P.path(m["file"]), h0, m.get("offset", 0), m.get("gain", -9)) if m else None
        for o in h.get("overlays", []):
            s = by_id[o["id"]]
            at, x, y = place(s, f(o["at"]) / sp, next(xi) if s["kind"] == "circle" else None)
            ovs.append((paths[o["id"]], at, x, y)); sfx += sfx_for(P, s, at)
        for sx in h.get("sfx", []):
            sfx.append((P.path(sx["file"]), f(sx["at"]) / sp, sx.get("gain", -14)))
    if cur:
        music.append((cur[0], cur[1], dur + 0.3, cur[3], cur[2]))
    audio.assemble_voice(P, pieces, P.work("voice_trailer.wav"))
    voice = audio.speed_audio(P, P.work("voice_trailer.wav"), P.work("voice_trailer_fast.wav"))
    audio.mix(P, voice, music, sfx, P.work("mix_trailer.wav"), duck_db=P.cfg["audio"]["trailer_duck_db"])
    vid = render.render_segments(P, C.fr, C.luts, segs, P.work("seg_trailer"), label="trailer")
    vid = render.speed_video(P, vid, P.work("seg_trailer_fast.mp4"))
    render.composite(P, vid, P.work("mix_trailer.wav"), ovs + logo_overlay(P, dur), P.work("part_trailer.mp4"), dur)
    print(f"  trailer part: {dur:.1f}s")
    return dur


def build_title(C):
    P = C.P
    t = P.cfg["title"]
    if not t:
        return 0.0
    spec = dict(id="title", kind="title", dur=t.get("dur", 3.0), line1=t.get("line1", ""), line2=t.get("line2", P.cfg["name"]))
    spec["logo"] = t.get("logo") or P.cfg["logo"]["file"]
    spec["credit"] = t.get("credit_image")
    paths = render_overlays(C, ov_specs(C, [spec]), only={"title"})
    n = int(spec["dur"] * 48000)
    sf.write(P.work("title_voice.wav"), np.zeros((n, 2), np.float32), 48000, subtype="PCM_24")
    m = t.get("music")
    audio.mix(P, P.work("title_voice.wav"), [(P.path(m["file"]), 0, spec["dur"], m.get("gain", -6), m.get("offset", 0))] if m else [],
              [], P.work("mix_title.wav"), duck_db=0)
    src = paths["title"]
    if tuple(P.res) != (1920, 1080):
        src = P.work("title_scaled.mp4")
        media.run(["ffmpeg", "-v", "error", "-y", "-i", paths["title"], "-vf", f"scale={P.res[0]}:{P.res[1]}", src])
    render.composite(P, src, P.work("mix_title.wav"), [], P.work("part_title.mp4"), spec["dur"], k=1)
    return spec["dur"]


# ---------------------------------------------------------------------- master
def build_master(C):
    P = C.P
    parts = []
    tdur = build_trailer(C) if P.cfg["trailer"]["hooks"] else 0.0
    if tdur:
        parts.append(P.work("part_trailer.mp4"))
    titdur = build_title(C)
    if titdur:
        parts.append(P.work("part_title.mp4"))
    build_episode(C)
    parts.append(P.work("part_episode.mp4"))
    lst = P.work("master_list.txt")
    with open(lst, "w") as fh:
        fh.writelines(f"file '{os.path.basename(p)}'\n" for p in parts)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", P.cfg["name"]) + "_Master.mp4"
    out = P.out(name)
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out])
    if tdur:
        tl = P.work("trailer_list.txt")
        with open(tl, "w") as fh:
            fh.writelines(f"file '{os.path.basename(p)}'\n" for p in parts[:-1])
        media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", tl, "-c", "copy", "-movflags", "+faststart",
                   P.out("trailer_preview.mp4")])
    off = tdur + titdur
    ch = json.load(open(P.work("episode_chapters.json")))["chapters"]
    fmt = lambda s: f"{int(s // 60):02d}:{int(s % 60):02d}"
    entries = [(off + t, nm) for _, t, nm in ch]
    if not entries or entries[0][0] >= 1:            # YouTube needs a chapter at 00:00
        entries.insert(0, (0, "Trailer" if tdur else "Intro"))
    lines = [f"{fmt(t)} {nm}" for t, nm in entries]
    open(P.out("youtube_chapters.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    d = media.probe(out)["duration"]
    print(f"\nMASTER: {out}  ({d / 60:.1f} min)")
    if lines:
        print("\n".join(lines))
    return out
