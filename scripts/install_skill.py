#!/usr/bin/env python3
"""Install the podcast-forge skill for Claude Code:  python scripts/install_skill.py [--dest DIR]
Copies skills/podcast-forge to ~/.claude/skills/podcast-forge (a personal `house-style.md` there is preserved)."""
import argparse
import shutil
from pathlib import Path

src = Path(__file__).resolve().parent.parent / "skills" / "podcast-forge"
ap = argparse.ArgumentParser()
ap.add_argument("--dest", default=str(Path.home() / ".claude" / "skills" / "podcast-forge"))
a = ap.parse_args()
dest = Path(a.dest)
dest.mkdir(parents=True, exist_ok=True)
for f in src.iterdir():
    shutil.copy2(f, dest / f.name)
print(f"Installed skill to {dest}")
print("Put your own standing preferences in house-style.md next to SKILL.md (it is never overwritten).")
