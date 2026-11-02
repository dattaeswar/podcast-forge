"""Project configuration: one YAML file describes the cameras, speakers, look and extras of an episode."""
import copy
import os
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


DEFAULTS = {
    "name": "Podcast",
    "output_dir": "out",
    "work_dir": "work",
    "fps": 30,
    "speed": 1.0,                    # 1.1 = 10 % faster, pitch preserved
    "resolution": [1920, 1080],
    "hwaccel": "auto",               # auto | none | d3d11va | cuda | videotoolbox | vaapi | qsv
    "encode": {"preset": "veryfast", "crf": 15, "final_preset": "medium", "final_crf": 18},
    "audio": {
        "source": None,              # camera whose audio carries the (wireless) mics; default = master camera
        "clean": True,               # highpass + denoise + de-ess + compress + limit
        "loudness": -14.0,           # LUFS target (YouTube/Spotify standard)
        "true_peak": -1.5,
        "denoise_db": 12,
        "music_duck_db": -14.0,      # music level while someone speaks (episode)
        "trailer_duck_db": -16.0,
    },
    "speech": {"hi_db": 19, "lo_db": 13},   # speech gate: dB above the noise floor (on / hold)
    "edit": {
        "long_pause": 0.60,          # silences longer than this are tightened to it
        "pre": 0.12, "post": 0.20,   # breathing room kept around each phrase
        "drop_fillers": True,        # remove isolated short bursts (aa, mm, breaths, false starts)
        "min_shot": 2.5, "max_shot": 7.5,
        "reaction_after": 18.0,      # cut to the listener after this many seconds of one speaker
        "seed": 11,                  # planner randomness (same seed -> same edit)
        "start": None, "end": None,  # raw master-time bounds of the episode body
        "cuts": [],                  # [[a, b], ...] raw master seconds to remove entirely
    },
    "speakers": [],
    "cameras": [],
    "framing": {"wide": 0.94, "close": 0.62, "tight": 0.50, "setup_breaks": [], "heads": {}},
    "grade": {"preset": "studio_warm", "match_amount": 0.7, "reference": None, "calibration_times": None,
              "params": {}, "lut": {}},
    "style": {"accent": [255, 153, 0], "accent2": [255, 210, 64],
              "fonts": {"display": "impact.ttf", "title": "bahnschrift.ttf", "body": "segoeuib.ttf"}},
    "diarize": {"motion_camera": None, "overrides": []},     # overrides: [[a, b, "speaker-id"], ...]
    "transcribe": {"enabled": True, "language": "en", "model": "large-v3-turbo", "device": "cpu"},
    "logo": {"file": None, "size": 170},
    "overlays": [],
    "chapters": [],
    "music": {"bed": None, "outro": None, "cues": []},
    "sfx": {"photo_click": None},
    "title": None,
    "trailer": {"hooks": [], "min_shot": 1.3, "max_shot": 3.2, "long_pause": 0.35},
    "reels": {"camera": None, "cta": None, "items": []},
    "thumbnails": None,
}


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Project:
    def __init__(self, path):
        path = Path(path)
        if path.is_dir():
            path = path / "project.yaml"
        if not path.exists():
            raise ConfigError(f"Project file not found: {path}  (create one with `pf init`)")
        self.file = path.resolve()
        self.root = self.file.parent
        self.cfg = deep_merge(DEFAULTS, yaml.safe_load(self.file.read_text(encoding="utf-8")) or {})
        self.fps = int(self.cfg["fps"])
        self.speed = float(self.cfg["speed"])
        self.res = tuple(self.cfg["resolution"])
        self._validate()

    # ------------------------------------------------------------------ paths
    def path(self, rel):
        """Resolve a path from the config relative to the project folder."""
        p = Path(os.path.expanduser(str(rel)))
        return str(p if p.is_absolute() else (self.root / p))

    def work(self, *parts):
        p = self.root / self.cfg["work_dir"] / Path(*parts) if parts else self.root / self.cfg["work_dir"]
        (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
        return str(p)

    def out(self, *parts):
        p = self.root / self.cfg["output_dir"] / Path(*parts) if parts else self.root / self.cfg["output_dir"]
        (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
        return str(p)

    # ------------------------------------------------------------------ cameras / speakers
    @property
    def cams(self):
        return self.cfg["cameras"]

    def cam(self, name):
        for c in self.cams:
            if c["name"] == name:
                return c
        raise ConfigError(f"Unknown camera '{name}'. Cameras: {[c['name'] for c in self.cams]}")

    @property
    def master(self):
        return next(c for c in self.cams if c.get("master"))

    @property
    def audio_cam(self):
        return self.cam(self.cfg["audio"]["source"] or self.master["name"])

    @property
    def speakers(self):
        return self.cfg["speakers"]

    def spk_index(self, sid):
        for i, s in enumerate(self.speakers):
            if s["id"] == sid:
                return i
        raise ConfigError(f"Unknown speaker '{sid}'. Speakers: {[s['id'] for s in self.speakers]}")

    def side(self, sid, cam):
        s = self.speakers[self.spk_index(sid)]
        return (s.get("positions") or {}).get(cam) or s["side"]

    def overlay(self, oid):
        for o in self.cfg["overlays"]:
            if o["id"] == oid:
                return o
        raise ConfigError(f"Overlay '{oid}' is referenced but not defined under `overlays:`")

    # ------------------------------------------------------------------ validation
    def _validate(self):
        c = self.cfg
        if not c["cameras"]:
            raise ConfigError("`cameras:` is empty. Add at least one camera with a `file:`.")
        names = [x.get("name") for x in c["cameras"]]
        if None in names or len(set(names)) != len(names):
            raise ConfigError("Every camera needs a unique `name:`.")
        masters = [x for x in c["cameras"] if x.get("master")]
        if not masters:
            c["cameras"][0]["master"] = True
        elif len(masters) > 1:
            raise ConfigError("Only one camera can be `master: true` (the timeline/audio reference).")
        for x in c["cameras"]:
            if "file" not in x:
                raise ConfigError(f"Camera '{x['name']}' has no `file:`.")
        if len(c["speakers"]) != 2:
            raise ConfigError("`speakers:` must list exactly two people (host and guest) with an `id`, `name` and `side`.")
        for s in c["speakers"]:
            if "id" not in s or ("side" not in s and "positions" not in s):
                raise ConfigError("Each speaker needs `id` and `side: L|R` (which side of the frame they sit on).")
        if self.cfg["audio"]["source"]:
            self.cam(self.cfg["audio"]["source"])

    def check_files(self):
        """Raise a friendly error listing every missing input file."""
        missing = [self.path(x["file"]) for x in self.cams if not os.path.exists(self.path(x["file"]))]
        for o in self.cfg["overlays"]:
            for k in ("image", "video"):
                if o.get(k) and not os.path.exists(self.path(o[k])):
                    missing.append(self.path(o[k]))
        if missing:
            raise ConfigError("Missing files:\n  " + "\n  ".join(missing))
