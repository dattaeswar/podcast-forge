"""Render text to a transparent PNG with correct shaping for complex scripts (Telugu, Hindi, Tamil, Arabic...).

Pillow only shapes complex scripts when built with libraqm; ffmpeg's drawtext never does. So:
  1. if Pillow has raqm -> use it (Linux / macOS usually)
  2. on Windows -> draw through GDI+ (System.Drawing), which shapes via Uniscribe
Use the PNG as `title.credit_image`, a thumbnail credit, or anywhere an image is accepted.
"""
import os
import subprocess
import sys
import tempfile

PS = r"""
param([string]$TextFile,[string]$Out,[string]$Font,[double]$Size,[string]$Fill,[string]$Stroke,[double]$StrokeW)
Add-Type -AssemblyName System.Drawing
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8).TrimEnd()
$fam = New-Object System.Drawing.FontFamily($Font)
$fmt = New-Object System.Drawing.StringFormat
$gp = New-Object System.Drawing.Drawing2D.GraphicsPath
$gp.AddString($text, $fam, 0, $Size, (New-Object System.Drawing.PointF(0,0)), $fmt)
$b = $gp.GetBounds()
$pad = [int]($StrokeW * 2 + 6)
$bmp = New-Object System.Drawing.Bitmap ([int]($b.Width + 2*$pad)), ([int]($b.Height + 2*$pad))
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAlias'
$m = New-Object System.Drawing.Drawing2D.Matrix
$m.Translate(-$b.X + $pad, -$b.Y + $pad); $gp.Transform($m)
if ($StrokeW -gt 0) {
  $pen = New-Object System.Drawing.Pen([System.Drawing.ColorTranslator]::FromHtml($Stroke), [single]($StrokeW*2))
  $pen.LineJoin = 'Round'; $g.DrawPath($pen, $gp)
}
$g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml($Fill))), $gp)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"""


def render(text, out, font=None, size=96, fill="#ffffff", stroke="#000000", stroke_width=0):
    """Write `text` as a transparent PNG. `font` is a family name on Windows (e.g. 'Nirmala UI', 'Gautami') or a
    .ttf path when Pillow has raqm."""
    from PIL import Image, ImageDraw, ImageFont, features
    if features.check("raqm") and (font is None or os.path.exists(str(font))):
        f = ImageFont.truetype(font, size, layout_engine=ImageFont.Layout.RAQM) if font else ImageFont.load_default(size)
        l, t, r, b = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=f, stroke_width=stroke_width)
        im = Image.new("RGBA", (r - l + 20, b - t + 20), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((10 - l, 10 - t), text, font=f, fill=fill, stroke_width=stroke_width, stroke_fill=stroke)
        im.save(out)
        return out
    if sys.platform != "win32":
        raise SystemExit("Complex-script text needs Pillow built with libraqm (pip install pillow --no-binary :all: with libraqm-dev), "
                         "or run on Windows where GDI+ is used.")
    with tempfile.TemporaryDirectory() as d:
        tf, sf_ = os.path.join(d, "t.txt"), os.path.join(d, "r.ps1")
        open(tf, "w", encoding="utf-8").write(text)
        open(sf_, "w", encoding="utf-8-sig").write(PS)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", sf_, "-TextFile", tf, "-Out", os.path.abspath(out),
                            "-Font", font or "Segoe UI", "-Size", str(size), "-Fill", fill, "-Stroke", stroke, "-StrokeW", str(stroke_width)],
                           capture_output=True, text=True)
        if r.returncode or not os.path.exists(out):
            raise SystemExit("GDI+ text rendering failed:\n" + r.stderr[-800:])
    return out
