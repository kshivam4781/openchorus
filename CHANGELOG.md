# Changelog

All notable changes to OpenChorus are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-09

First public release.

### Added
- **Create:** Simple mode (one description) and Custom mode (your lyrics, style and title),
  two takes per create (1 to 4), Songs and Sounds, instrumental toggle, length up to
  10 minutes, BPM, key, seed and 50+ vocal languages.
- **Planner:** a local Ollama model writes a distinct title, structured lyrics and a
  production brief for every take, validated before rendering.
- **More options:** vocal gender, Weirdness, Style influence, take variety and Exclude styles.
- **Max Mode:** renders twice as many candidates and keeps the best by AI judge score
  plus lyric clarity.
- **Mastering:** every take is normalised to -14 LUFS / -1 dBTP with a 24-bit WAV kept.
- **Sound check:** audiobox-aesthetics quality rating and a Whisper lyric clarity score per song.
- **Cover art:** SSD-1B + LCM-LoRA paints a 1024x1024 cover per take; repaint with a prompt
  or upload your own image.
- **Use a picture:** a local vision model turns a photo into a song idea.
- **Remix tools:** Extend, Cover in a new style, Remaster, Replace section, Personas, Upload.
- **Edit:** crop, cut a section, fade in or out, change speed, reverse.
- **Stems:** 4 stems (vocals, drums, bass, other) or 2 stems with Demucs.
- **Downloads:** MP3 (320 kbps), WAV (24-bit) and MP4 video with cover art and waveform.
- **Library:** search, like, dislike, rename, delete, date groups and live progress.
- **AI service:** optional GPU sidecar (`ai_service/`) for the judge, cover art, Whisper and Demucs.
- **Engines:** ACE-Step 1.5 over its REST API, plus an offline demo synth for trying the app
  without a GPU.
- **Tooling:** benchmark scripts (`tools/bench_*.py`), a resumable model fetcher, Windows
  start/stop scripts, Docker and Docker Compose files.
- **Tests:** 50 API and engine-contract tests plus a Playwright end-to-end run.

[Unreleased]: https://github.com/kshivam4781/openchorus/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/kshivam4781/openchorus/releases/tag/v1.0.0
