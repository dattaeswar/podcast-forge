"""Fast tests on synthetic data (a 2-second ffmpeg test video stands in for footage)."""
import shutil
import subprocess

import numpy as np
import pytest
import yaml

from podcastforge import build, grade, speech
from podcastforge.config import ConfigError, Project
from podcastforge.framing import Framing
from podcastforge.plan import Planner

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_project(tmp_path, extra=None):
    for n in ("a", "b"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264", "-c:a", "aac",
                        str(tmp_path / f"{n}.mp4")], check=True)
    cfg = {"name": "T", "resolution": [640, 360],
           "cameras": [{"name": "a", "file": "a.mp4", "master": True}, {"name": "b", "file": "b.mp4"}],
           "speakers": [{"id": "host", "side": "R"}, {"id": "guest", "side": "L"}],
           "framing": {"heads": {"a": {"host": [0.7, 0.4], "guest": [[0, 0.3, 0.4], [100, 0.4, 0.5]]},
                                 "b": {"host": [0.7, 0.4], "guest": [0.3, 0.4]}}}}
    cfg.update(extra or {})
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg))
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "sync.json").write_text('{"master": "a", "cameras": {"a": {"offset": 0, "drift": 0}, "b": {"offset": 1.0, "drift": 0}}}')
    return Project(tmp_path)


# ------------------------------------------------------------------ config
def test_defaults_and_master(tmp_path):
    (tmp_path / "project.yaml").write_text(yaml.safe_dump({
        "cameras": [{"name": "x", "file": "x.mp4"}], "speakers": [{"id": "a", "side": "L"}, {"id": "b", "side": "R"}]}))
    P = Project(tmp_path)
    assert P.master["name"] == "x" and P.cfg["edit"]["long_pause"] == 0.6 and P.speed == 1.0


@pytest.mark.parametrize("bad, msg", [
    ({"cameras": [], "speakers": []}, "cameras"),
    ({"cameras": [{"name": "x", "file": "x"}], "speakers": [{"id": "a", "side": "L"}]}, "exactly two"),
    ({"cameras": [{"name": "x", "file": "x", "master": True}, {"name": "y", "file": "y", "master": True}],
      "speakers": [{"id": "a", "side": "L"}, {"id": "b", "side": "R"}]}, "master"),
])
def test_config_errors_are_friendly(tmp_path, bad, msg):
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError, match=msg):
        Project(tmp_path)


# ------------------------------------------------------------------ speech trimming
def test_pieces_trim_pauses_and_fillers():
    sp = [[0.0, 2.0], [2.2, 4.0], [10.0, 10.2], [20.0, 22.0]]       # short isolated burst at 10 s = filler
    pcs = speech.pieces_for(sp, [[0, 30]], long_pause=0.6, pre=0.1, post=0.1, drop_fillers=True, fps=30)
    assert len(pcs) == 2 and pcs[0][0] <= 0.0 + 1e-6 and 3.9 < pcs[0][1] < 4.3
    assert all(abs(a * 30 - round(a * 30)) < 1e-6 for p in pcs for a in p)       # frame-snapped
    keep = speech.pieces_for(sp, [[0, 30]], 0.6, 0.1, 0.1, False, 30)
    assert len(keep) == 3


def test_episode_ranges_apply_cuts(tmp_path):
    P = make_project(tmp_path, {"edit": {"start": 10, "cuts": [[20, 30], [50, 55]]}}) if shutil.which("ffmpeg") else None
    if P is None:
        pytest.skip("ffmpeg")
    assert speech.episode_ranges(P, 100) == [[10.0, 20.0], [30.0, 50.0], [55.0, 100.0]]


# ------------------------------------------------------------------ framing / planner
@needs_ffmpeg
def test_framing_crops_are_valid_and_follow_heads(tmp_path):
    fr = Framing(make_project(tmp_path))
    for shot in fr.shots:
        cam = fr.cam_of(shot)
        W, H = fr.size[cam]
        for t in (0.0, 1.0, 1.9):
            x, y, w, h = fr.crop(shot, t)
            assert x >= 0 and y >= 0 and x + w <= W and y + h <= H and w % 2 == 0 and h % 2 == 0
            assert abs(w / h - 16 / 9) < 0.02
    assert fr.head("a", "guest", 50)[0] == pytest.approx(0.35, abs=1e-6)         # linear interpolation
    assert fr.side("a:guest") == "L" and fr.side("a:wide") is None and fr.scale("a:host+") == 2


@needs_ffmpeg
def test_setup_break_stops_interpolation(tmp_path):
    P = make_project(tmp_path, {"framing": {"setup_breaks": [50], "heads": {
        "a": {"host": [[0, .7, .4], [40, .8, .4], [60, .5, .4], [100, .6, .4]], "guest": [.3, .4]},
        "b": {"host": [.7, .4], "guest": [.3, .4]}}}})
    fr = Framing(P)
    assert fr.head("a", "host", 45)[0] == pytest.approx(0.8)       # held, not blended toward 0.5 across the break
    assert fr.head("a", "host", 55)[0] == pytest.approx(0.5)


@needs_ffmpeg
def test_planner_covers_every_frame_and_never_repeats_a_look(tmp_path):
    P = make_project(tmp_path)
    fr = Framing(P)
    speech_runs = [[i * 3.0, i * 3.0 + 2.6] for i in range(60)]
    spk = np.zeros(2000, np.int8); spk[600:1200] = 1
    pl = Planner(P, fr, speech_runs, spk)
    pieces = speech.pieces_for(speech_runs, [[0, 180]], 0.6, 0.1, 0.2, True, 30)
    segs = pl.plan(pieces, 2.5, 7.5, 18)
    assert sum(s["frames"] for s in segs) == sum(round((b - a) * 30) for a, b in pieces)
    jumps = [(a["shot"], b["shot"]) for a, b in zip(segs, segs[1:]) if abs(a["o"] - b["i"]) < 1e-6]
    assert jumps and all(a != b for a, b in jumps)
    assert {s["spk"] for s in segs} == {"host", "guest"}


@needs_ffmpeg
def test_same_seed_same_edit(tmp_path):
    P = make_project(tmp_path)
    fr = Framing(P)
    runs = [[i * 3.0, i * 3.0 + 2.6] for i in range(30)]
    pcs = speech.pieces_for(runs, [[0, 90]], 0.6, 0.1, 0.2, True, 30)
    a = Planner(P, fr, runs, np.zeros(1000, np.int8)).plan(pcs, 2.5, 7.5, 18)
    b = Planner(P, fr, runs, np.zeros(1000, np.int8)).plan(pcs, 2.5, 7.5, 18)
    assert [s["shot"] for s in a] == [s["shot"] for s in b]


@needs_ffmpeg
def test_lock_layout_moves_speaker_out_from_under_circle(tmp_path):
    P = make_project(tmp_path)
    (tmp_path / "work" / "speech.json").write_text('{"speech": [[0, 1]], "floor_db": -60}')

    class C:  # minimal context
        pass
    c = C(); c.P = P; c.fr = Framing(P)
    segs = [dict(i=0, o=2, shot="a:guest", spk="guest", frames=60, push=True), dict(i=2, o=4, shot="a:host", spk="host", frames=60, push=False)]
    xs, changed = build.lock_layout(c, segs, [(0.0, 4.0)])
    assert len(xs) == 1 and changed >= 1
    assert len({build.XPOS[c.fr.side(s["shot"])] for s in segs}) == 1            # one circle position valid for the whole window


# ------------------------------------------------------------------ transcript search / colour
def test_find_time_occurrences():
    words = [[1.0, 1.2, "hello"], [2.0, 2.2, "Jagan"], [5.0, 5.2, "then"], [9.0, 9.3, "jagan"]]
    assert build.find_time(words, "jagan", "first") == 2.0
    assert build.find_time(words, "jagan", "last") == 9.0
    assert build.find_time(words, "jagan", 2) == 9.0
    assert build.find_time(words, "nothing") is None


def test_look_is_bounded_and_monotonic():
    x = np.linspace(0, 1, 256)[:, None] * np.ones((1, 3))
    for name, p in grade.PRESETS.items():
        if p:
            y = grade.look(x.copy(), p)
            assert y.min() >= 0 and y.max() <= 1
            assert np.all(np.diff(y.mean(1)) >= -1e-3)


def test_cube_has_expected_size(tmp_path):
    f = tmp_path / "x.cube"
    grade.write_cube(str(f), lambda c: c)
    lines = f.read_text().splitlines()
    assert lines[0] == "LUT_3D_SIZE 33" and len(lines) == 1 + 33 ** 3
