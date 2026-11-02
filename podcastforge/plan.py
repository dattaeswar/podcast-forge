"""Shot planner: turns phrases + speaker labels into a multicam edit decision list.

Rules (all tunable under `edit:`):
- cut on pauses and speaker changes (J-cut: the picture follows the new voice by 0.2 s)
- never repeat the same camera + size twice in a row, so every jump cut is hidden by a change of shot
- after `reaction_after` seconds of one speaker, cut to a 2.5 s listener reaction
- long shots get a slow sub-pixel push-in
"""
import numpy as np


class Planner:
    def __init__(self, P, fr, speech, spk):
        self.P, self.fr, self.speech, self.spk = P, fr, speech, spk
        self.fps = P.fps
        self.rng = np.random.RandomState(P.cfg["edit"]["seed"])
        self.names = [s["id"] for s in P.speakers]
        self.choices = {}
        self.listener = {}
        user = P.cfg.get("planner", {}).get("weights", {}) if isinstance(P.cfg.get("planner"), dict) else {}
        for i, sid in enumerate(self.names):
            other = self.names[1 - i]
            w = {}
            cams = [c["name"] for c in P.cams]
            share = {"close": 0.52, "tight": 0.14, "wide": 0.34}
            for kind, suffix in (("close", ""), ("tight", "+")):
                shots = [f"{c}:{sid}{suffix}" for c in cams if f"{c}:{sid}{suffix}" in fr.shots]
                tot = sum(1 / (k + 1) for k in range(len(shots)))
                for k, s in enumerate(shots):
                    w[s] = share[kind] * (1 / (k + 1)) / tot
            wides = [f"{c}:wide" for c in cams]
            for k, s in enumerate(wides):
                w[s] = share["wide"] / len(wides)
            if sid in user:
                w = {k: float(v) for k, v in user[sid].items() if k in fr.shots}
            tot = sum(w.values())
            self.choices[sid] = (list(w), [v / tot for v in w.values()])
            self.listener[other] = [f"{c}:{sid}" for c in cams if f"{c}:{sid}" in fr.shots] + [wides[-1]]

    def q(self, t):
        return round(t * self.fps) / self.fps

    def spk_at(self, a, b):
        s = self.spk[int(a * 10):max(int(a * 10) + 1, int(b * 10))]
        return self.names[int(s.mean() > 0.5)] if len(s) else self.names[0]

    def same_look(self, a, b):
        return bool(b) and self.fr.cam_of(a) == self.fr.cam_of(b) and self.fr.scale(a) == self.fr.scale(b)

    def pick(self, sid, prev, punch=False):
        names, w = self.choices[sid]
        for _ in range(50):
            s = self.rng.choice(names, p=w)
            if punch and self.fr.scale(s) != 2 and self.rng.rand() < 0.7:
                continue
            if s != prev and not self.same_look(s, prev):
                return s
        alt = [n for n in names if n != prev]
        return alt[0] if alt else names[0]

    def plan(self, pieces, minlen, maxlen, react_after, punch_ranges=()):
        """-> list of dicts {i, o (raw master seconds), shot, spk, frames, push, reaction?}"""
        q, fps = self.q, self.fps
        speech = self.speech
        segs, prev, run_spk, run_len = [], None, None, 0.0
        for ps, pe in pieces:
            pauses = [(e + s2) / 2 for (s1, e), (s2, e2) in zip(speech, speech[1:]) if ps < e and s2 < pe and s2 - e > 0.12]
            bounds = [ps]
            lab = [self.spk_at(x, x + 0.1) for x in np.arange(ps, pe, 0.1)]
            i = 0
            while i < len(lab):
                j = i
                while j < len(lab) and lab[j] == lab[i]:
                    j += 1
                if i > 0 and (j - i) >= 12 and lab[i] != lab[i - 1]:      # runs < 1.2 s are backchannels: ignored
                    bounds.append(q(ps + i * 0.1 + 0.2))
                i = j
            bounds.append(pe)
            bounds = sorted(set(bounds))
            for a, b in zip(bounds, bounds[1:]):
                sid = self.spk_at(a, b)
                t = a
                while t < b - 1e-6:
                    if sid != run_spk:
                        run_spk, run_len = sid, 0.0
                    remain = b - t
                    target = self.rng.uniform(minlen + 1.0, maxlen)
                    react = run_len > react_after and remain > 2.5 + minlen
                    cut = None
                    if remain > maxlen or react:
                        cands = [p for p in pauses if t + minlen <= p <= min(b - minlen * 0.6, t + maxlen)]
                        if react:
                            cands = [p for p in pauses if t + 1.0 <= p <= b - 2.6] or cands
                        if cands:
                            cut = q(min(cands, key=lambda p: abs(p - (t + (1.0 if react else target)))))
                    end = cut if cut else b
                    if react and cut:
                        if cut - t >= 0.7:
                            shot = self.pick(sid, prev)
                            segs.append(dict(i=t, o=cut, shot=shot, spk=sid)); prev = shot
                        else:
                            cut = t
                        r_end = q(min(b, cut + 2.5))
                        opts = [s for s in self.listener[sid] if s != prev and not self.same_look(s, prev)] or self.listener[sid]
                        sh = str(self.rng.choice(opts))
                        segs.append(dict(i=cut, o=r_end, shot=sh, spk=sid, reaction=True))
                        prev, t, run_len = sh, r_end, 0.0
                        continue
                    punch = any(pa <= t < pb for pa, pb in punch_ranges)
                    shot = self.pick(sid, prev, punch)
                    segs.append(dict(i=t, o=end, shot=shot, spk=sid))
                    run_len += end - t
                    prev, t = shot, end
        out, edges = [], {p[0] for p in pieces}
        for s in segs:                                                  # absorb <0.7 s flashes into the previous shot
            if out and s["o"] - s["i"] < 0.7 and s["i"] not in edges and abs(out[-1]["o"] - s["i"]) < 1e-6:
                out[-1]["o"] = s["o"]
            else:
                out.append(s)
        for s in out:
            d = s["o"] - s["i"]
            s["push"] = bool(d >= 3.5 and self.fr.scale(s["shot"]) >= 1 and not s.get("reaction") and self.rng.rand() < 0.5)
            s["frames"] = int(round(d * fps))
        return out
