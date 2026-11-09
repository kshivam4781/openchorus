# Third-party notices

OpenChorus's own code is released under the [MIT License](LICENSE). It does not
bundle model weights. The models below are downloaded by you, from their official
sources, the first time a feature needs them, and each stays under its own license.
Check each license before using generated output commercially.

## Models

| Component | Used for | License | Source |
|---|---|---|---|
| ACE-Step 1.5 (DiT + 5 Hz LM) | Music generation | MIT | https://github.com/ace-step/ACE-Step-1.5 |
| SSD-1B | Cover art | Apache-2.0 | https://huggingface.co/segmind/SSD-1B |
| LCM-LoRA for SSD-1B | Fast cover art | OpenRAIL++-M | https://huggingface.co/latent-consistency/lcm-lora-ssd-1b |
| audiobox-aesthetics | Quality judge | CC-BY-4.0 | https://github.com/facebookresearch/audiobox-aesthetics |
| Demucs (htdemucs) | Stem separation | MIT | https://github.com/facebookresearch/demucs |
| Whisper large-v3-turbo (faster-whisper) | Lyric clarity | MIT | https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo |
| Llama 3.1 (via Ollama) | Lyrics and production briefs | Llama 3.1 Community License | https://ollama.com/library/llama3.1 |
| LLaVA (via Ollama) | Image inspiration | Apache-2.0 | https://ollama.com/library/llava |

You can swap the Ollama models for any other local model with `OLLAMA_MODEL` and
`OLLAMA_VISION_MODEL`.

## Software

| Package | License |
|---|---|
| FastAPI, Starlette | MIT / BSD-3-Clause |
| Uvicorn | BSD-3-Clause |
| HTTPX | BSD-3-Clause |
| NumPy, SciPy | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| python-multipart | Apache-2.0 |
| PyTorch | BSD-3-Clause |
| Diffusers, Transformers, Accelerate, PEFT | Apache-2.0 |
| faster-whisper | MIT |
| FFmpeg (called as an external program) | LGPL-2.1+ / GPL depending on build |
| Playwright (tests only) | Apache-2.0 |
| Bricolage Grotesque, IBM Plex Sans (web fonts, Google Fonts) | SIL Open Font License 1.1 |

## Generated music

Rights in songs you create depend on the model licenses above and the law where you
live. OpenChorus claims no rights over your output. Do not use it to imitate a real
artist's voice or to reproduce copyrighted lyrics or melodies.
