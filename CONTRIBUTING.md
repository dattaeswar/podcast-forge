# Contributing

Thanks for helping make podcast editing accessible to everyone.

```bash
git clone https://github.com/dattaeswar/podcast-forge && cd podcast-forge
pip install -e ".[dev]"
pytest -q            # fast, uses a 2-second synthetic video
```

- Keep stages independent: each reads `work/*` and writes `work/*`.
- New knobs go in `config.DEFAULTS` **and** `examples/project.example.yaml` (and the copy in `podcastforge/`).
- Add a test for pure logic (planner, trimming, framing, layout). Rendering is checked by eye on a short clip.
- Good first issues: 3+ speaker support, more grade presets, more overlay kinds, Linux/macOS testing, caption burn-in.

Please do not commit footage, music or personal photos. `.gitignore` already excludes `work/`, `out/`, media and `project.yaml`.
