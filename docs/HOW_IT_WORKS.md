# How it works

Every stage reads the previous stage's files in `work/` so you can inspect, edit and re-run any step.

| Stage | Command | Module | Output |
|---|---|---|---|
| Sync | `pf sync` | `sync.py` | `work/sync.json` |
| Speech | `pf speech` | `speech.py` | `work/speech.json` |
| Heads | `pf heads` / `pf sheet` | `heads.py` | `work/heads.json` |
| Speakers | `pf speakers` | `diarize.py` | `work/spk.npy` (10 Hz, 0 = speaker 0) |
| Transcript | `pf transcribe` | `transcribe.py` | `work/words.json` |
| Grade | `pf grade` | `grade.py` | `work/lut/<cam>.cube`, `grade_preview.jpg` |
| Edit | `pf edl` | `plan.py`, `build.py` | `work/edl_*.json`, `out/timeline_*.xml` |
| Render | `pf build` | `render.py`, `overlays.py`, `audio.py` | `out/*_Master.mp4` |
| Reels | `pf reels` | `reels.py` | `out/reels/*.mp4` |
| Thumbnails | `pf thumbs` | `thumbs.py` | `out/thumbnails/*.jpg` |

Timebase convention: every time in `project.yaml` and in `work/` is **master-camera time** unless noted. A camera's own time is `t_cam = t_master × (1 + drift) + offset`.

## Sync
The scratch audio of each camera is cross-correlated with the master's using GCC-PHAT (phase-transform weighting makes the correlation peak razor sharp even with room echo). It samples five 2-minute windows across the recording, rejects low-confidence peaks (sharpness ≤ 3), and fits a line to detect clock drift. Drift below 0.02 ms per second is treated as zero.

## Speech detection
10 ms frame energy with a hysteresis gate relative to the recording's own noise floor (5th percentile): speech starts when it rises `hi_db` above the floor and stops after 120 ms below `lo_db`. Runs closer than 150 ms are merged.

## Tightening
`pieces_for` turns speech runs into phrases: gaps longer than `long_pause` are removed, isolated bursts shorter than 0.45 s with silence on both sides (fillers, breaths, false starts) are dropped, each phrase is padded by `pre`/`post` and snapped to the frame grid so audio and video are frame-exact.

## Who is speaking
1. MFCC + delta features (19 coefficients ×2) per 10 ms frame, z-scored.
2. Two 16-component diagonal GMMs seeded from `speakers[i].seed` (or the heuristics in `diarize.py`), refined for three iterations by re-seeding from the most confident 15 % / 40 % of one-second windows.
3. Per-window log-likelihood ratio → 10 Hz score.
4. If head positions are known, per-speaker **face-motion energy** (frame differencing in a box around each head) is fused in (weight 0.8) **only when it agrees with the voice model more than 55 % of the time on speech**.
5. Hand-written `diarize.overrides` are applied last.

## Shot planning
`plan.py` walks each phrase. It cuts on pauses or speaker changes (the picture follows the new voice by 0.2 s: a J-cut), picks the next shot from weighted choices per speaker, and refuses to repeat the same camera and size twice in a row so that every jump cut lands on a visibly different frame. After `reaction_after` seconds of one speaker it cuts to a 2.5 s listener shot. Shots of 3.5 s or more get a slow push-in implemented with ffmpeg's `perspective` filter (sub-pixel, so no `zoompan` jitter). The planner is seeded (`edit.seed`), so the same inputs give the same edit.

Shot names are `<camera>:wide`, `<camera>:<speaker>`, `<camera>:<speaker>+` (tight). Crops follow interpolated head keyframes, with look-room toward the other person.

## Circle layout lock
A circle is placed on the side opposite the speaker. For each circle window `lock_layout` picks the position that is free for most of the window and replaces any shot that would put a face under it: first another framing of the same person, then a cutaway to the other person, then a wide.

## Colour
All cameras are matched to the reference camera by moving Lab mean/standard deviation toward it (Reinhard transfer, 70 % by default), then one shared look (black lift, exposure, warm highlights / cool shadows, S-curve, vibrance, highlight roll-off) is applied. The result is baked into a 33³ `.cube` per camera and applied with `lut3d` (tetrahedral interpolation).

## Audio
`highpass 80 → afftdn → deesser → dynaudnorm → acompressor → alimiter` on the voice source. Voice is assembled from phrases with 6 ms fades, sped up with `rubberband` (pitch preserved; `atempo` fallback), music is ducked by an attack/release envelope of the voice, and the mix is normalised in two passes (measure, then linear gain) to the target loudness.

## Overlays
Rendered once into alpha `.mov` clips with Pillow (animated ring, count-up numbers, word-by-word text) and cached by content hash in `work/ov/`. Placement uses the transcript: `when.keywords` is a regex on the lower-cased word stream, `occurrence` picks the n-th match. Overlays of the same kind never stack.

## Rendering
Each shot is its own ffmpeg process (hardware decode → crop → LUT → Lanczos scale → optional push-in → unsharp → vignette → x264). Segments are named by a hash of everything that affects them, so changing one shot re-renders only that shot. Segments are concatenated with stream copy, sped up, and composited with the overlays and mixed audio in a final pass.
