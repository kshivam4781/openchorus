# Changelog

All notable changes to OpenChorus are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.2] - 2026-11-09

Cross-platform fixes for macOS, Linux and Windows.

### Added
- The launchers check for Python 3.10 or newer and look for `python3.13` down to `python3.10`
  first, so a Mac with the old built-in Python 3.9 uses the Homebrew one. Clear help when
  Python or the venv module is missing.
- AI service on Apple Silicon: cover art runs on the Mac GPU (MPS). The setup script installs
  the CPU build of PyTorch on Linux and Windows machines without an NVIDIA card.
- CI runs the tests on macOS and Python 3.13 too, checks the shell scripts with shellcheck,
  and starts and stops the app with the one-click scripts on Linux, macOS and Windows.
- README: one-line prerequisite installs per system, and more troubleshooting entries.

### Changed
- `start_all` turns on ACE-Step's large-chunk GPU audio decoding only on NVIDIA cards with
  11 GB or more. Smaller cards keep ACE-Step's memory-safe defaults instead of running out
  of memory.
- All logs from `start_all.bat` now go to the `logs` folder, like on macOS and Linux.
- `setup_acestep` installs ACE-Step and exits; `start_all` starts it.
- Docker Compose runs ACE-Step with the 0.6B planner and loads the models at startup (its
  image defaults to the 4B planner, which needs far more memory).

### Fixed
- `stop_all.sh` could leave ACE-Step running
  on macOS, which has no `setsid`. Each part now runs in its own process group on both
  systems, and `stop_all.sh` waits for it and force-stops it if needed.
- `start_all.bat` failed when `uv` had just been installed (not yet on PATH) and when run
  without a console, where `timeout` does not work. It now waits until the app answers
  before opening the browser.
- `start_all` now installs new requirements after an update (it only did so on the first run).
- `start.sh` opened the browser too early and tried `xdg-open` on machines without a desktop.

## [1.0.1] - 2026-11-09

### Added
- `start_all.sh`, `stop_all.sh` and `setup_ai_service.sh` for macOS and Linux.
- README sections on hardware requirements (with the tested laptop) and troubleshooting.

### Fixed
- `start.sh` and the other shell scripts were not executable in the repository, so
  `./start.sh` failed on macOS and Linux.
- ACE-Step could exit at startup on Windows when `start_all.bat` sent its output to a log
  file (emoji in a cp1252 console). It now runs with UTF-8 output.
- Docker: documented how to reach an ACE-Step server on the host.

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

[Unreleased]: https://github.com/kshivam4781/openchorus/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/kshivam4781/openchorus/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/kshivam4781/openchorus/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/kshivam4781/openchorus/releases/tag/v1.0.0
