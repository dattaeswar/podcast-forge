"""Head-position tools: a contact sheet for reading positions by eye, and optional automatic face detection
(OpenCV YuNet, a 230 KB ONNX model downloaded once to ~/.cache/podcastforge)."""
import json
import os
import urllib.request

import numpy as np

from . import media, sync as syncmod

MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
MODEL = os.path.join(os.path.expanduser("~"), ".cache", "podcastforge", "face_detection_yunet_2023mar.onnx")


def contact_sheet(P, times=None, n=6):
    """Write work/sheet_<cam>.jpg: a grid of frames with a 10 % coordinate grid, for reading head x/y by eye."""
    import cv2
    out = []
    for c in P.cams:
        path = P.path(c["file"])
        dur = media.probe(path)["duration"]
        ts = times or list(np.linspace(dur * 0.05, dur * 0.95, n))
        tiles = []
        for t in ts:
            f = P.work("frames", f"{c['name']}_{int(t)}.jpg")
            media.grab_frame(path, float(t), f, scale="640:-1")
            im = cv2.imread(f)
            h, w = im.shape[:2]
            for i in range(1, 10):
                cv2.line(im, (int(w * i / 10), 0), (int(w * i / 10), h), (255, 255, 255), 1)
                cv2.line(im, (0, int(h * i / 10)), (w, int(h * i / 10)), (255, 255, 255), 1)
                cv2.putText(im, f".{i}", (int(w * i / 10) + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
                cv2.putText(im, f".{i}", (2, int(h * i / 10) - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
            cv2.putText(im, f"{c['name']} t={int(t)}s", (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
            tiles.append(im)
        cols = 3
        while len(tiles) % cols:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
        dst = P.work(f"sheet_{c['name']}.jpg")
        cv2.imwrite(dst, np.vstack(rows))
        out.append(dst)
    return out


def _model():
    if not os.path.exists(MODEL):
        os.makedirs(os.path.dirname(MODEL), exist_ok=True)
        print(f"  downloading face model (230 KB) from {MODEL_URL}")
        urllib.request.urlretrieve(MODEL_URL, MODEL)
    return MODEL


def detect(P, step=45.0):
    """Sample every `step` seconds, find the two faces, assign them to the speakers by left/right order.
    Writes work/heads.json: {camera: {speaker: [[t, x, y], ...]}} (camera time)."""
    import cv2
    det = cv2.FaceDetectorYN.create(_model(), "", (640, 360), 0.6, 0.3, 5000)
    result = {}
    for c in P.cams:
        path = P.path(c["file"])
        dur = media.probe(path)["duration"]
        rows = {s["id"]: [] for s in P.speakers}
        for t in np.arange(step / 2, dur - 1, step):
            f = P.work("frames", f"det_{c['name']}.jpg")
            media.grab_frame(path, float(t), f, scale="960:-1")
            im = cv2.imread(f)
            h, w = im.shape[:2]
            det.setInputSize((w, h))
            _, faces = det.detect(im)
            if faces is None or not len(faces):
                continue
            faces = sorted(faces, key=lambda r: -r[2] * r[3])[:2]
            faces = sorted(faces, key=lambda r: r[0])                      # left -> right
            pts = [((r[0] + r[2] / 2) / w, (r[1] + r[3] / 2) / h) for r in faces]
            lefts = [s["id"] for s in P.speakers if P.side(s["id"], c["name"]) == "L"]
            rights = [s["id"] for s in P.speakers if P.side(s["id"], c["name"]) == "R"]
            if len(pts) == 2 and lefts and rights:
                rows[lefts[0]].append([float(t), *pts[0]]); rows[rights[0]].append([float(t), *pts[1]])
            elif len(pts) == 1 and lefts and rights:
                sid = lefts[0] if pts[0][0] < 0.5 else rights[0]
                rows[sid].append([float(t), *pts[0]])
        for sid, r in rows.items():
            if len(r) >= 5:                                                # median-smooth jitter
                a = np.array(r)
                for j in (1, 2):
                    pad = np.pad(a[:, j], 2, mode="edge")
                    a[:, j] = [np.median(pad[i:i + 5]) for i in range(len(a))]
                rows[sid] = a.round(3).tolist()
            if not rows[sid]:
                raise SystemExit(f"Face detection found nobody on the {P.side(sid, c['name'])} of camera '{c['name']}'. "
                                 f"Use `pf sheet` and set framing.heads by hand.")
        result[c["name"]] = rows
        print(f"  {c['name']}: " + ", ".join(f"{k} {len(v)} samples" for k, v in rows.items()))
    json.dump(result, open(P.work("heads.json"), "w"))
    return result
