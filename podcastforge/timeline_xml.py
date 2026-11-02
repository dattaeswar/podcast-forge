"""Export the multicam edit as Final Cut Pro 7 XML (opens in Premiere Pro, DaVinci Resolve, Final Cut) so the
automatic cut can be fine-tuned by hand. V1 = camera picks as full source frames (crops/push-ins live in the
rendered MP4 only), A1 = the cleaned master voice."""
import os
from xml.sax.saxutils import escape

from . import sync as syncmod


def _url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/").replace(" ", "%20")


def xmeml(P, fr, segs, voice, path, name, cams):
    fps = P.fps
    total = sum(s["frames"] for s in segs)
    seen, clips, pos = set(), [], 0
    for n, s in enumerate(segs):
        cam = fr.cam_of(s["shot"])
        w, h = fr.size[cam]
        fin = int(round(syncmod.cam_time(fr.sync, cam, s["i"]) * fps))
        if cam in seen:
            ref = f'<file id="{cam}"/>'
        else:
            ref = (f'<file id="{cam}"><name>{escape(os.path.basename(cams[cam]))}</name><pathurl>{escape(_url(cams[cam]))}</pathurl>'
                   f'<rate><timebase>{fps}</timebase></rate><media><video><samplecharacteristics><width>{w}</width>'
                   f'<height>{h}</height></samplecharacteristics></video></media></file>')
        clips.append(f'<clipitem id="v{n}"><name>{escape(s["shot"])}</name><start>{pos}</start><end>{pos + s["frames"]}</end>'
                     f'<in>{fin}</in><out>{fin + s["frames"]}</out>{ref}</clipitem>')
        seen.add(cam); pos += s["frames"]
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence><name>{escape(name)}</name>'
           f'<duration>{total}</duration><rate><timebase>{fps}</timebase></rate><media><video><format><samplecharacteristics>'
           f'<width>{P.res[0]}</width><height>{P.res[1]}</height></samplecharacteristics></format><track>{"".join(clips)}</track></video>'
           f'<audio><track><clipitem id="a1"><name>master voice</name><start>0</start><end>{total}</end><in>0</in><out>{total}</out>'
           f'<file id="voice"><name>{escape(os.path.basename(voice))}</name><pathurl>{escape(_url(voice))}</pathurl></file>'
           f'</clipitem></track></audio></media></sequence></xmeml>\n')
    open(path, "w", encoding="utf-8").write(xml)
