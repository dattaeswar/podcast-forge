# Troubleshooting

**`Could not sync camera`**: both cameras must hear the same room. Add a hand clap at the start, or set `sync: {offset: <seconds>}` on the camera (`t_cam = t_master + offset`).

**Host and guest look swapped / the wrong person is shown**: check `pf speakers` output ("X speaks N %"). Give each speaker a `seed` (a stretch where only they talk), or fix stretches with `diarize.overrides: [[start, end, host]]`. If face motion disagrees with the voice model the log says it is ignored.

**Crops cut off a head**: head positions are wrong. Run `pf sheet`, read the coordinates from the 0.1 grid, set `framing.heads`, or try `pf heads`. If people re-seated, set `framing.setup_breaks`.

**A circle covers a face**: the layout lock handles this automatically. If it still happens the speaker is on the same side in every camera and no alternative framing exists; add a camera or move the overlay with `when.at`.

**A photo circle misses the faces in the photo**: set `focus: [x, y]` (fractions) on that overlay, and `fit: 1.3` to show more of the picture.

**An overlay never appears**: `pf build` prints `(overlay 'x' not placed ...)`. The keyword regex found nothing in the transcript. Use `pf search "pattern"` to see how the recogniser spelled it, or place it with `when: {at: <seconds>}`.

**Telugu / Hindi / Arabic text looks broken**: Pillow and ffmpeg `drawtext` do not shape complex scripts. Render the text with `pf text "..." out.png --font "Nirmala UI"` and use the PNG (`title.credit_image`, `thumbnails.credit_image`).

**Music is claimed by Content ID**: use tracks you have a licence for. The pipeline never ships or downloads music.

**`rubberband` missing**: speed-up falls back to `atempo` (works, slightly lower quality). Install an FFmpeg *full* build.

**Hardware decode errors**: set `hwaccel: none`.

**Re-render after a change**: segments and overlays are cached by content hash, so just re-run `pf build master`. To start clean, delete `work/`.

**Transcription is slow**: CPU `large-v3-turbo` runs ≈ 1× real time. Set `transcribe.model: small` for speed, `transcribe.enabled: false` to skip (then place overlays with `when.at`).
