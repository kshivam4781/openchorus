# OpenChorus roadmap

What is built, what comes next, and ideas that would take OpenChorus past Suno.
Everything here runs locally on open models.

## Done

**Phase 1: sound quality**
- Local LLM planner (Ollama) writes a distinct title, lyrics and a 45 to 90 word production
  brief per take, with section structure checked before rendering.
- High quality mode: ACE-Step's language model plans the audio ("thinking") on every take.
- Mastering on every take: two-pass loudness normalisation to -14 LUFS / -1 dBTP, low cut,
  soft limiter, 320 kbps MP3 plus a 24-bit 48 kHz WAV.
- AI quality judge (audiobox-aesthetics) rates every song.
- Lyric clarity: Whisper transcribes each take and scores how many requested words can be heard.
- Max Mode: render 2x candidates, keep the best by judge score plus lyric clarity.
- Benchmarked engine settings with fixed songs (tools/bench_*.py): ACE-Step's 0.6B planner LM
  beat the 1.7B one on this GPU (judge 7.97 vs 7.77, lyric word error 0.59 vs 0.87), so it stays.
- Weirdness and Style influence map to the language model's temperature and guidance.

**Phase 2: cover art**
- SSD-1B + LCM-LoRA paints a 1024x1024 cover per take from a planner-written art prompt.
- Repaint with your own prompt, or upload an image (auto-cropped square).
- Edits, covers, extensions and stems inherit the parent's artwork.

**Phase 3: Suno feature parity**
- Songs and Sounds, More options (vocal gender, weirdness, style influence, variety, exclude styles).
- Remaster, Edit (crop, cut, fades, speed, reverse), section menu on the waveform.
- 4-stem split (vocals, drums, bass, other).
- MP3 / WAV / MP4 video downloads, dislike, "Use a picture" image inspiration.

## Next (Phase 5)

| Feature | How | Why it beats Suno |
|---|---|---|
| Synced lyrics (karaoke view, LRC export) | Whisper word timestamps on the vocal stem | Suno shows plain lyrics only on most songs |
| Auto re-roll | When every Max Mode candidate has low lyric clarity, render more before giving up | Fixes the most common AI-song flaw: mumbled lines |
| Best-section stitching | Score 10-second windows of each take, repaint weak sections | A step beyond picking whole takes |
| Playlists, queue and radio | SQLite tables plus a queue in the player; radio keeps generating in a chosen style | Unlimited, never-repeating station |
| Workspaces | Folder per project, move and filter songs | Same as Suno, but local |
| Song page | Shareable read-only page per song on the LAN | Share with people at home without uploading |
| LAN sharing with sign-in | Optional password and user accounts so a family or band can share one GPU | No subscription per person |
| Mobile PWA | Manifest and offline shell; installable on a phone over the LAN | |

## Later (Phase 6): beyond Suno

| Feature | How |
|---|---|
| Train on your own music | LoRA fine-tuning of ACE-Step on a folder of your songs (ACE-Step ships a LoRA trainer). Personas become real voice and style models. |
| Multitrack studio | ACE-Step `lego` and `extract` tasks: add a track (drums, bass, strings) to an existing song, or pull one out; a simple timeline to arrange sections. |
| MIDI export | Basic Pitch (Spotify, Apache-2.0) on the stems to give editable MIDI for a DAW. |
| Mashup | Cover task with two reference clips: the melody of one and the style of the other. |
| Speech and narration | A TTS tab (e.g. Kokoro or F5-TTS) for intros, podcasts and spoken word over a generated bed. |
| Vocal-only re-sing | Keep the instrumental, regenerate only the vocal with new lyrics (repaint on the vocal stem, then remix). |
| Reference-track matching | Analyse an uploaded song's tempo, key and loudness and match the master to it. |
| Smarter judge | Train a small preference model on your likes and dislikes, so Max Mode learns your taste. |
| Batch and API mode | Queue 50 jingles from a CSV; an OpenAI-style API for other apps. |
| Video | Lyric videos with synced words and animated cover art. |

## Engine upgrades to watch

- ACE-Step XL (4B) turbo: higher fidelity, needs about 12 GB of free VRAM or CPU offload.
- ACE-Step's 1.7B and 4B language models for better song structure and lyric adherence.
- New open music models as they appear: OpenChorus talks to engines through one small
  interface (`server/engines/base.py`), so adding one is a single file.
