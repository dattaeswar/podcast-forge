#!/usr/bin/env python3
"""Make any AI coding agent podcast-forge aware.

  python scripts/install_agent_files.py --dest path/to/my-episode      # copy AGENTS.md, GEMINI.md, CLAUDE.md there
  python scripts/install_agent_files.py --regenerate                   # rebuild AGENTS.md from skills/podcast-forge/SKILL.md
"""
import argparse
import re
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
skill = root / "skills" / "podcast-forge" / "SKILL.md"
HEADER = """# Podcast editing with podcast-forge (`pf`)

Instructions for AI coding agents (Codex, Gemini CLI, Claude Code, Cursor, Aider, Grok and others).
Prerequisites: Python 3.10+, FFmpeg (full build) on PATH, `pip install git+https://github.com/dattaeswar/podcast-forge`
(add `[transcribe]` and `[thumbs]` extras when needed). Run `pf doctor` first. Run commands from the episode folder.
The `house-style.md` mentioned below is optional; if it is absent, ignore that line.

"""

ap = argparse.ArgumentParser()
ap.add_argument("--dest")
ap.add_argument("--regenerate", action="store_true")
a = ap.parse_args()
if a.regenerate:
    body = re.sub(r"^---.*?---\s*", "", skill.read_text(encoding="utf-8"), flags=re.S)
    (root / "AGENTS.md").write_text(HEADER + body, encoding="utf-8")
    print("AGENTS.md regenerated")
if a.dest:
    d = Path(a.dest)
    d.mkdir(parents=True, exist_ok=True)
    for f in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
        shutil.copy2(root / f, d / f)
    print(f"Copied agent instruction files to {d}")
