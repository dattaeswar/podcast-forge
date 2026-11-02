"""`pf` command line. Typical run:  pf init → edit project.yaml → pf run   (or the stages one by one, see `pf --help`)."""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .config import ConfigError, Project

HERE = Path(__file__).resolve().parent.parent


def load(args):
    try:
        return Project(args.project)
    except ConfigError as e:
        raise SystemExit(f"config error: {e}")


def stage(name):
    print(f"\n== {name}", flush=True)
    return time.time()


def done(t0):
    print(f"   ({time.time() - t0:.0f}s)", flush=True)


# ------------------------------------------------------------------ stages
def cmd_init(a):
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    tpl = HERE / "examples" / "project.example.yaml"
    if not tpl.exists():                                    # installed as a wheel: template lives in the package
        tpl = Path(__file__).parent / "project.example.yaml"
    dst = d / "project.yaml"
    if dst.exists():
        raise SystemExit(f"{dst} already exists")
    shutil.copy(tpl, dst)
    for sub in ("footage", "photos", "music", "sfx", "fonts"):
        (d / sub).mkdir(exist_ok=True)
    print(f"Created {dst}\nPut footage in {d / 'footage'}, photos in photos/, then edit project.yaml and run `pf doctor`.")


def cmd_doctor(a):
    import importlib
    ok = True

    def line(label, good, extra=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  [{'ok' if good else '!!'}] {label} {extra}")
    print(f"podcast-forge {__version__} on Python {sys.version.split()[0]}")
    for t in ("ffmpeg", "ffprobe"):
        line(t, shutil.which(t), shutil.which(t) or "- install FFmpeg (full build)")
    if shutil.which("ffmpeg"):
        import subprocess
        filt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        line("ffmpeg rubberband (pitch-preserving speed-up)", "rubberband" in filt, "" if "rubberband" in filt else "- missing: falls back to atempo")
        for f in ("lut3d", "deesser", "afftdn", "perspective", "loudnorm"):
            line(f"ffmpeg filter {f}", f in filt)
    for m, extra in (("numpy", ""), ("scipy", ""), ("soundfile", ""), ("librosa", ""), ("sklearn", "scikit-learn"), ("cv2", "opencv-python"),
                     ("PIL", "pillow"), ("yaml", "pyyaml")):
        try:
            importlib.import_module(m); line(f"python {m}", True)
        except ImportError:
            line(f"python {m}", False, f"- pip install {extra or m}")
    for m, extra in (("faster_whisper", "transcribe"), ("rembg", "thumbs")):
        try:
            importlib.import_module(m); line(f"optional {m}", True)
        except ImportError:
            print(f"  [--] optional {m}: pip install \"podcast-forge[{extra}]\"")
    if a.project and Path(a.project).exists():
        P = load(a)
        try:
            P.check_files(); line("project files", True)
        except ConfigError as e:
            line("project files", False, f"\n{e}")
    sys.exit(0 if ok else 1)


def cmd_sheet(a):
    from . import heads
    P = load(a)
    for f in heads.contact_sheet(P):
        print("wrote", f)
    print("Read each person's head centre from the 0.1 grid (x across, y down) and put it in framing.heads, or run `pf heads`.")


def cmd_heads(a):
    from . import heads
    P = load(a)
    t = stage("detect heads"); heads.detect(P, a.step); done(t)


def cmd_sync(a):
    from . import sync
    P = load(a); P.check_files()
    if len(P.cams) == 1:
        print("single camera - nothing to sync"); return
    t = stage("sync cameras"); sync.sync_cameras(P); done(t)


def cmd_speech(a):
    from . import audio, speech
    P = load(a)
    t = stage("speech detection")
    wav = audio.raw16(P)
    runs, floor, _ = speech.detect(wav, P.cfg["speech"]["hi_db"], P.cfg["speech"]["lo_db"])
    speech.save(P, runs, floor)
    print(f"  noise floor {floor:.1f} dB; {len(runs)} speech runs, {sum(b - a for a, b in runs) / 60:.1f} min of speech")
    done(t)


def cmd_speakers(a):
    from . import diarize
    from .framing import Framing
    P = load(a)
    t = stage("who is speaking")
    motion = None
    try:
        fr = Framing(P)
        t0 = time.time()
        motion = diarize.motion_energy(P, fr)
        print(f"  face-motion cue computed ({time.time() - t0:.0f}s)")
    except SystemExit as e:
        print(f"  (no motion cue: {e})")
    diarize.run(P, motion=motion)
    done(t)


def cmd_transcribe(a):
    from . import media, transcribe
    P = load(a)
    t = stage("transcribe")
    transcribe.run(P, media.probe(P.path(P.master["file"]))["duration"])
    done(t)


def cmd_search(a):
    from . import transcribe
    transcribe.search(load(a), a.pattern)


def cmd_grade(a):
    from . import grade
    P = load(a); P.check_files()
    t = stage("colour grade"); grade.build(P); done(t)


def cmd_edl(a):
    from . import build
    P = load(a)
    t = stage("edit decisions"); build.build_edl(build.Ctx(P)); done(t)


def cmd_build(a):
    from . import build
    P = load(a)
    C = build.Ctx(P)
    t = stage(f"build {a.what}")
    {"trailer": build.build_trailer, "episode": build.build_episode, "master": build.build_master}[a.what](C)
    done(t)


def cmd_reels(a):
    from . import build, reels
    t = stage("reels"); reels.build_all(build.Ctx(load(a)), set(a.names) or None); done(t)


def cmd_thumbs(a):
    from . import build, thumbs
    P = load(a)
    t = stage("thumbnails"); thumbs.build(P, build.Ctx(P)); done(t)


def cmd_text(a):
    from . import textimg
    print("wrote", textimg.render(a.text, a.out, a.font, a.size, a.fill, a.stroke, a.stroke_width))


def cmd_run(a):
    """Everything from raw footage to master, skipping stages whose outputs already exist (use --force to redo)."""
    from . import audio, build, diarize, grade, heads, media, speech, sync, transcribe
    from .framing import Framing
    P = load(a); P.check_files()
    W = lambda *p: os.path.exists(P.work(*p))
    if len(P.cams) > 1 and (a.force or not W("sync.json")):
        t = stage("sync cameras"); sync.sync_cameras(P); done(t)
    if a.force or not W("speech.json"):
        cmd_speech(a)
    if not P.cfg["framing"]["heads"] and (a.force or not W("heads.json")):
        t = stage("detect heads"); heads.detect(P); done(t)
    if a.force or not W("spk.npy"):
        cmd_speakers(a)
    t = stage("clean voice"); audio.master_voice(P); done(t)
    if P.cfg["transcribe"]["enabled"] and (a.force or not W("words.json")):
        try:
            cmd_transcribe(a)
        except SystemExit as e:
            print(f"  (skipping transcript: {e})")
    if a.force or not all(W("lut", f"{c['name']}.cube") for c in P.cams):
        t = stage("colour grade"); grade.build(P); done(t)
    C = build.Ctx(P)
    t = stage("edit decisions"); build.build_edl(C); done(t)
    t = stage("build master"); build.build_master(C); done(t)
    if P.cfg["reels"]["items"]:
        from . import reels
        t = stage("reels"); reels.build_all(C); done(t)
    if P.cfg["thumbnails"]:
        from . import thumbs
        t = stage("thumbnails"); thumbs.build(P, C); done(t)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pf", description="podcast-forge: multicam podcast auto-editor")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-p", "--project", default="project.yaml", help="project.yaml or its folder (default: ./project.yaml)")
    sp = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, **kw):
        s = sp.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        return s
    s = add("init", cmd_init, "create a project folder with a template project.yaml"); s.add_argument("dir", nargs="?", default=".")
    add("doctor", cmd_doctor, "check ffmpeg, Python packages and project files")
    add("sheet", cmd_sheet, "write gridded contact sheets to read head positions by eye")
    s = add("heads", cmd_heads, "auto-detect both heads on every camera (downloads a 230 KB face model once)"); s.add_argument("--step", type=float, default=45.0)
    add("sync", cmd_sync, "align all cameras to the master by audio")
    add("speech", cmd_speech, "detect speech activity")
    add("speakers", cmd_speakers, "label who is speaking when")
    add("transcribe", cmd_transcribe, "word-level transcript (faster-whisper)")
    s = add("search", cmd_search, "find when something was said"); s.add_argument("pattern")
    add("grade", cmd_grade, "match cameras and build LUTs")
    add("edl", cmd_edl, "plan shots / cuts, write timeline XML")
    s = add("build", cmd_build, "render trailer / episode / master"); s.add_argument("what", choices=["trailer", "episode", "master"], nargs="?", default="master")
    s = add("reels", cmd_reels, "render 9:16 reels"); s.add_argument("names", nargs="*")
    add("thumbs", cmd_thumbs, "render YouTube thumbnails and reel covers")
    s = add("text", cmd_text, "render complex-script text (Telugu, Hindi, ...) to a transparent PNG")
    s.add_argument("text"); s.add_argument("out"); s.add_argument("--font"); s.add_argument("--size", type=int, default=96)
    s.add_argument("--fill", default="#ffffff"); s.add_argument("--stroke", default="#000000"); s.add_argument("--stroke-width", type=float, default=0)
    s = add("run", cmd_run, "run every stage"); s.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except ConfigError as e:
        raise SystemExit(f"config error: {e}")


if __name__ == "__main__":
    main()
