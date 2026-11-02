# podcast-forge

**Turn raw multi-camera podcast footage into a finished episode, trailer, vertical reels and thumbnails, from one YAML file.**

You film two people on two (or more) cameras. `podcast-forge` syncs the cameras, finds who is speaking, cuts the dead air and fillers, switches cameras like a human editor would, colour-matches everything, masters the audio to −14 LUFS, drops in photos and stat cards when topics come up, and exports an FCP7 XML so you can fine-tune in Premiere / Resolve / Final Cut.

It started as the pipeline behind the [Datta Talks](https://github.com/dattaeswar) podcast, and is open so anyone can edit their own show with their own look, photos, music and rules.

```
raw cameras ──► sync ──► speech + speakers ──► tighten cuts ──► multicam plan ──► grade ──► overlays ──► master
                                  │                                                              │
                              transcript ─► photos / stats / chapters on the words spoken         ├─► trailer (cold open)
                                                                                                   ├─► 9:16 reels
                                                                                                   └─► thumbnails
```

**Website: [dattaeswar.github.io/podcast-forge](https://dattaeswar.github.io/podcast-forge/)** (features, uses, 20-second demo video)

## What you get

| | |
|---|---|
| **Multicam switching** | Wide, close-up and tight punch-in per camera; never the same look twice in a row (so every jump cut is hidden), listener reaction shots after long monologues, J-cuts, slow sub-pixel push-ins. |
| **Tight pacing** | Long pauses shortened, isolated "aa / mm" and false starts removed, optional 1.1× speed-up with pitch preserved. |
| **Voice-first audio** | Uses the camera with your mic audio. High-pass, denoise, de-ess, compress, limit, then two-pass loudness to **−14 LUFS / −1.5 dBTP**. Music ducks under speech. |
| **Colour** | Every camera matched to a reference camera, then one shared look (`studio_warm`, `neutral`, `cinematic`, `punchy`, or your own `.cube`). Exported as 3D LUTs. |
| **Photo circles** | A photo appears in an animated ring beside the speaker **at the moment its topic is spoken** (matched on the transcript), and the shot underneath is reframed so the circle never covers a face. |
| **Stat cards, kinetic text, name tags, chapter tags** | Animated lower-thirds, count-up numbers, word-by-word text. |
| **Trailer + title card** | Hand-picked hooks cut into a cold open, followed by an animated title card. |
| **Reels / Shorts** | 9:16 re-frames of any hook or time range, centred on whoever speaks, with hook title, CTA and logo placed above the app UI. |
| **Thumbnails** | YouTube + reel covers with both people cut out, headline in a safe zone, topic photos, banner, logo and credit line. |
| **Any language** | Transcribe in any Whisper language. `pf text` renders Telugu / Hindi / Tamil / Arabic… with correct shaping for titles and credits. |
| **Hand-off** | FCP7 XML of the multicam cut, `youtube_chapters.txt`, LUTs, trailer preview. |

## Install

Requirements: Python 3.10+, **FFmpeg (full build, with `rubberband`)** on your PATH.

```bash
git clone https://github.com/dattaeswar/podcast-forge
cd podcast-forge
pip install .                           # core (or: pip install "git+https://github.com/dattaeswar/podcast-forge")
pip install ".[transcribe]"             # + faster-whisper (word timestamps for photo placement)
pip install ".[thumbs]"                 # + rembg (cut people out for thumbnails)
pf doctor                               # checks everything
```

No GPU required. Hardware video decode is used when available (`hwaccel: auto`).

## Quick start

```bash
pf init my-episode && cd my-episode     # creates project.yaml + folders
# 1. copy your footage into footage/ and describe it in project.yaml (cameras, speakers)
pf sheet                                # gridded contact sheets -> read head positions, or:
pf heads                                # auto-detect faces (downloads a 230 KB model once)
pf run                                  # everything, skipping finished stages
```

Outputs land in `out/`: `<Name>_Master.mp4`, `trailer_preview.mp4`, `youtube_chapters.txt`, `timeline_episode.xml`, `reels/*.mp4`, `thumbnails/*.jpg`.

Or run stage by stage and inspect each one:

```bash
pf sync          # align cameras by audio (GCC-PHAT), reports offset / drift
pf speech        # speech activity
pf speakers      # who talks when (voice model + on-camera motion)
pf transcribe    # word-level transcript
pf search "revenue|funding"      # find the time something was said, to place photos / hooks
pf grade         # match cameras, build LUTs, work/grade_preview.jpg
pf edl           # plan cuts + shots, write timeline XML
pf build master  # render everything   (or: pf build trailer | episode)
pf reels         # 9:16 cuts
pf thumbs        # thumbnails
```

## The project file

One `project.yaml` describes the episode. The minimum is two cameras and two speakers:

```yaml
cameras:
  - {name: wide, file: footage/a.mp4, master: true}
  - {name: side, file: footage/b.mov}
audio: {source: side}                 # the camera that heard the wireless mics
speakers:
  - {id: host,  name: Alex Rivera, role: Host,    side: R, seed: [[12, 22]]}
  - {id: guest, name: Sam Carter,  role: Founder, side: L}
```

Everything else (photos on topics, chapters, music, trailer hooks, reels, thumbnails, colour, pacing) is optional and documented with comments in [`examples/project.example.yaml`](examples/project.example.yaml).

### The four things that decide quality

1. **`speakers[].seed`**: a few seconds (≥ 5 s) where *only* that person talks. The voice model is seeded from it. Without seeds, speaker 0 is assumed to open the show and speaker 1 to talk longest. Check the printed share and fix errors with `diarize.overrides`.
2. **Head positions**: `pf heads` (automatic) or `pf sheet` (read by eye). Crops are built from them, so they matter. Set `framing.setup_breaks` if people re-seat or a camera moves.
3. **`edit.start` / `edit.cuts`**: skip pre-roll chatter and remove phone calls or retakes by master-time ranges.
4. **`overlays[].focus`**: tells the circle crop where the faces are in each photo so it never misses them.

## Use it with an AI agent

You can let an AI coding agent run the whole workflow: it writes `project.yaml` from your footage, picks trailer hooks by reading the transcript, looks at frames, and iterates on notes like "the circle covers his face". Any agent that can run shell commands works. Install the tool once, then give the agent the instructions in this repo.

```bash
pip install "git+https://github.com/dattaeswar/podcast-forge"          # the `pf` command (needs FFmpeg on PATH)
pip install "podcast-forge[transcribe,thumbs] @ git+https://github.com/dattaeswar/podcast-forge"   # with optional extras
git clone https://github.com/dattaeswar/podcast-forge                   # to get the agent instruction files
```

| Agent | Install | Then |
|---|---|---|
| **Claude Code** | `python scripts/install_skill.py` (installs the skill to `~/.claude/skills/podcast-forge`) | Open Claude Code in your episode folder: *"Edit this podcast with the podcast-forge skill: footage is in ./footage"* |
| **OpenAI Codex CLI** | `python scripts/install_agent_files.py --dest my-episode` (copies `AGENTS.md`) | `cd my-episode && codex` and ask it to edit the podcast. Codex reads `AGENTS.md` from the working folder. |
| **Gemini CLI** | same command as above (copies `GEMINI.md` + `AGENTS.md`) | `cd my-episode && gemini` and ask. Gemini CLI loads `GEMINI.md`, which imports `AGENTS.md`. |
| **Cursor, Windsurf, Copilot agent, Aider, Grok and others** | same command as above | Most read `AGENTS.md` automatically. If yours does not, start with *"Read AGENTS.md and follow it to edit the podcast in ./footage"* (Aider: `aider --read AGENTS.md`). |

Notes:
- `AGENTS.md` is generated from [`skills/podcast-forge/SKILL.md`](skills/podcast-forge/SKILL.md) (`python scripts/install_agent_files.py --regenerate`). Edit the skill, not `AGENTS.md`.
- Put your own standing preferences (look, pace, music rules) in a `house-style.md` next to the instruction file; the agent reads it first.
- The agent needs shell access and permission to run `ffmpeg` / `pf`; long renders (a 40-minute episode can take an hour or more on a laptop CPU, transcription alone runs near real time) should run in the background.
- Agent behaviour differs by product and changes over time: if one does not pick up the file, tell it to read `AGENTS.md` explicitly.

## Docs

- [How it works](docs/HOW_IT_WORKS.md): the algorithms behind each stage
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Contributing](CONTRIBUTING.md)

## Limits (read before you start)

- **Two speakers, front-facing cameras.** Panels of 3+ are not supported yet.
- **Speaker labelling is heuristic.** It is good with seeds and a clean mic mix, weaker on cross-talk. Overrides exist for a reason.
- **Whisper is imperfect on code-switched speech** (e.g. Tenglish / Hinglish): English mode keeps names and brands; native-script modes can be unreliable. Use `pf search` and `when.at` for placements the transcript misses.
- Tested on Windows 11. Linux / macOS should work (hardware decode falls back to software) but have had less testing.
- `pf heads` face detection and `pf thumbs` background removal download small models on first use.

## Music, fonts and rights

No music, sound effects, fonts or footage ship with this repo. Bring tracks you have the rights to; film BGMs and fan edits can trigger Content ID claims. Fonts are looked up by name in your system font folders and `./fonts`; any `.ttf` works.

## License

MIT © Datta Eswar Tangeti. See [LICENSE](LICENSE).
