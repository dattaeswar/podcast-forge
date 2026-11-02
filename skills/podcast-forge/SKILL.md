---
name: podcast-forge
description: Edit multi-camera podcasts end to end with the podcast-forge CLI (`pf`). Use when the user has raw podcast / interview footage (2 speakers, 1-3 cameras) and wants a finished episode, trailer, reels/Shorts, thumbnails, colour grade, chapters or YouTube titles and descriptions; or asks to tweak a previous podcast-forge edit.
---

# podcast-forge: editing a podcast with the `pf` CLI

First read `house-style.md` next to this file if it exists: it holds this user's standing preferences and overrides anything below.

## What you are driving
`pf` takes `project.yaml` (cameras, speakers, look, overlays, music, trailer hooks, reels, thumbnails) and produces the master, trailer, 9:16 reels, thumbnails, FCP7 XML and chapters. Each stage writes to `work/` and can be re-run. Docs: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/TROUBLESHOOTING.md`, annotated `examples/project.example.yaml`. Repo location: ask the user or `pip show podcast-forge`.

## Workflow (do not skip the checks)
1. **Intake**: list footage, `ffprobe` each file, confirm which camera has the wireless-mic audio, who sits on which side (L/R as seen in frame), any re-seating (`setup_breaks`), and what overlay photos / music / logo exist. Ask only what you cannot see.
2. **`pf init`**, then write `project.yaml`. Use fictional-free, real values; copy the example file's structure.
3. **`pf doctor`**: fix missing tools first.
4. **`pf sync`**: expect "N/N confident points". A non-zero drift is real; zero is normal.
5. **Heads**: `pf heads` (downloads a 230 KB face model once, say so first) else `pf sheet` and read the grid. **View the sheet / a few cropped frames**: wrong heads ruin every crop.
6. **`pf speech`**, then seeds: find a stretch where only the host talks (≥ 5 s), put it under `speakers[0].seed`, run **`pf speakers`**. Read the printed shares; sanity-check 3-4 moments by listening or by transcript.
7. **`pf transcribe`** (use language `en` for code-switched speech: names survive). Use **`pf search "regex"`** to find moments for photos, stat cards, chapters and trailer hooks.
8. Fill `overlays` (use `when.keywords`, fall back to `when.at`), `chapters`, `music`, `trailer.hooks` (strongest quotable moments, 5-15 s each), `reels.items`, `thumbnails`.
9. **`pf grade`**: open `work/grade_preview.jpg`; adjust `grade.preset/params` until both cameras look like one.
10. **`pf edl`**, then **`pf build master`**. Always **look at frames**: extract a contact sheet with ffmpeg at overlay times and check circles never cover a face, logo is bottom-right, text is on screen long enough. Measure loudness (`ffmpeg -af ebur128`): target −14 LUFS.
11. **`pf reels`**, **`pf thumbs`** when configured. Show the user the thumbnails and one reel frame.
12. Deliver: paths of master, chapters file, reels, thumbnails; draft YouTube title/description/tags (chapters from `out/youtube_chapters.txt`).

## Rules of thumb
- Prefer re-running one stage over editing outputs; segments/overlays are cached by hash so re-builds are cheap.
- Be token-frugal: read logs with `tail`, look at one contact sheet rather than many frames.
- Never claim success without checking duration, loudness and a frame sheet.
- Do not add whooshes/SFX the user did not ask for. Do not ship music; ask for licensed tracks and warn about Content ID for film BGMs.
- Non-Latin text (Telugu, Hindi, …) must go through `pf text` (PNG); drawtext/Pillow mis-shape it.
- Publishing (GitHub, YouTube) is the user's call: draft, then ask.

## Common edits
| Request | Change |
|---|---|
| "tighter / looser" | `edit.long_pause`, `edit.min_shot/max_shot`, `edit.drop_fillers` |
| "faster pace" | `speed: 1.1` |
| "circle covers face" | check `framing.heads`; give the photo a `focus` |
| "photo misses faces" | overlay `focus`, `fit: 1.3` |
| "different colour" | `grade.preset` / `grade.params` / own `.cube` |
| "remove a section" | `edit.cuts: [[a, b]]` (master seconds) |
| "wrong person on screen" | speaker `seed`s or `diarize.overrides` |
| "new reel" | add `reels.items` + hook(s) in `trailer.hooks` |
