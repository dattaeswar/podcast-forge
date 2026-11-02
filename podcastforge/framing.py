"""Shots and crops. A shot is (camera, subject speaker or None for wide, crop-height fraction).

Shot names:  "<cam>:wide"   "<cam>:<speaker>"   "<cam>:<speaker>+"  (the + is a tight punch-in)
Head positions are normalised (x, y) keyframes in each camera's own timeline; they interpolate linearly, and a
"setup break" (people re-seated, camera moved) stops interpolation across it.
"""
import json
import os

import numpy as np

from . import media, sync as syncmod

SCALE = {"wide": 0, "close": 1, "tight": 2}


class Framing:
    def __init__(self, P):
        self.P = P
        self.sync = syncmod.load(P) if len(P.cams) > 1 else {P.cams[0]["name"]: {"offset": 0.0, "drift": 0.0}}
        self.size = {}
        for c in P.cams:
            info = media.probe(P.path(c["file"]))
            self.size[c["name"]] = (info["width"], info["height"])
        fr = P.cfg["framing"]
        self.breaks = sorted(fr.get("setup_breaks") or [])
        self.aspect = P.res[0] / P.res[1]
        self.keys = self._load_keys(fr.get("heads") or {})
        self.shots = {}
        for c in P.cams:
            cf = {**{k: fr[k] for k in ("wide", "close", "tight")}, **(c.get("framing") or {})}
            self.shots[f"{c['name']}:wide"] = (c["name"], None, cf["wide"], "wide")
            for s in P.speakers:
                self.shots[f"{c['name']}:{s['id']}"] = (c["name"], s["id"], cf["close"], "close")
                if cf.get("tight") and c.get("tight", True):
                    self.shots[f"{c['name']}:{s['id']}+"] = (c["name"], s["id"], cf["tight"], "tight")

    def _load_keys(self, cfg_heads):
        keys = {}
        auto = {}
        f = self.P.work("heads.json")
        if os.path.exists(f):
            auto = json.load(open(f))
        for c in self.P.cams:
            for s in self.P.speakers:
                k = (cfg_heads.get(c["name"]) or {}).get(s["id"]) or (auto.get(c["name"]) or {}).get(s["id"])
                if not k:
                    raise SystemExit(f"No head position for speaker '{s['id']}' on camera '{c['name']}'. "
                                     f"Run `pf heads` (auto-detect) or set framing.heads.{c['name']}.{s['id']} in project.yaml.")
                if len(k) == 2 and not isinstance(k[0], (list, tuple)):
                    k = [[0.0, k[0], k[1]]]
                keys.setdefault(c["name"], {})[s["id"]] = sorted(tuple(map(float, p)) for p in k)
        return keys

    # ------------------------------------------------------------------ geometry
    def cam_break(self, cam):
        return [syncmod.cam_time(self.sync, cam, b) for b in self.breaks]

    def head(self, cam, sid, t):
        """Interpolated head centre (x, y), normalised, at camera time t."""
        k = self.keys[cam][sid]
        brks = self.cam_break(cam)
        zone = sum(t >= b for b in brks)
        sel = [p for p in k if sum(p[0] >= b for b in brks) == zone] or k
        ts = [p[0] for p in sel]
        return float(np.interp(t, ts, [p[1] for p in sel])), float(np.interp(t, ts, [p[2] for p in sel]))

    def crop(self, shot, t):
        """(x, y, w, h) in source pixels of `shot` at camera time t, even-sized for the encoder."""
        cam, sid, frac, _ = self.shots[shot]
        W, H = self.size[cam]
        ch = frac * H
        cw = ch * self.aspect
        if cw > W:
            cw, ch = W, W / self.aspect
        if sid is None:
            pts = [self.head(cam, s["id"], t) for s in self.P.speakers]
            cx, cy = np.mean([p[0] for p in pts]) * W, min(p[1] for p in pts) * H + 0.30 * ch
        else:
            hx, hy = self.head(cam, sid, t)
            lead = 0.42 if self.P.side(sid, cam) == "L" else 0.58      # look-room toward the other person
            cx, cy = hx * W + (0.5 - lead) * cw, hy * H + (0.5 - 0.36) * ch
        x = float(np.clip(cx - cw / 2, 0, W - cw)); y = float(np.clip(cy - ch / 2, 0, H - ch))
        return int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2

    # ------------------------------------------------------------------ queries used by the planner / layout lock
    def cam_of(self, shot):
        return self.shots[shot][0]

    def subject(self, shot):
        return self.shots[shot][1]

    def scale(self, shot):
        return SCALE[self.shots[shot][3]]

    def side(self, shot):
        """'L' | 'R' | None (wide): which side of the frame the subject sits on, i.e. where a circle must NOT go."""
        cam, sid, _, _ = self.shots[shot]
        return None if sid is None else self.P.side(sid, cam)
