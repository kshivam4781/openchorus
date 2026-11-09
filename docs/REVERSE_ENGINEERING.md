# How OpenChorus was "reverse engineered" (clean room)

OpenChorus is an independent, open-source song studio that offers the same
*kind* of workflow people know from hosted AI music services such as Suno.
It is not affiliated with Suno, contains no Suno code, assets or API calls,
and does not talk to Suno in any way.

## Method

The build followed the loop popularised by the "Reverse Engineer Anything"
skills (REA by @morluto, and Speedrun's self-learning-loop skill):

1. Examine the target's observable behaviour.
2. Write a must-have checklist.
3. Build your own version.
4. Test against the checklist and iterate until every item passes.

### What was examined, and what was not

Only **public, observable behaviour** was used: Suno's help pages, public
guides and tutorials describing the product's UI and features.

We deliberately did **not**:

* decompile, de-minify or trace Suno's web bundle or mobile apps,
* capture or replay Suno's private network API,
* use any Suno account, cookie or credit.

REA's own README limits it to software "you own or are authorized to
examine", and Suno's terms of service prohibit reverse engineering the
service. A feature study plus a clean-room implementation on an openly
licensed model gives the same end result without those problems.

REA *is* useful on this project's own code and on the open-source model
(e.g. inspecting ACE-Step's `.safetensors` headers or the app's own JS), and
`docs/USING_REA.md` shows how.

## Feature parity checklist

Status is verified by `tests/test_api.py` (API level, demo engine),
`tests/test_acestep_contract.py` (request/response contract against a fake
server that mirrors ACE-Step 1.5's own route code) and `tests/run_e2e.py`
(real Chrome via Playwright). All 18 items pass; stems pass in the
"Demucs not installed" path and need `pip install demucs` to run for real.

| # | Suno-style feature (public behaviour) | OpenChorus implementation | Engine path | Test |
|---|---|---|---|---|
| 1 | **Simple mode**: one description box, AI writes lyrics + style | `mode: "simple"` | ACE-Step `sample_query` + `thinking` | api, e2e |
| 2 | **Custom mode**: separate Lyrics, Style of Music, Title | `mode: "custom"` | ACE-Step `prompt` + `lyrics` | api, e2e |
| 3 | **Instrumental toggle** | `instrumental: true` → lyrics `[Instrumental]` | ACE-Step docs convention | api |
| 4 | **Two variations per create** | `variations` (default 2, max 4) | `batch_size` | api, e2e |
| 5 | **Generate lyrics** button | `/api/lyrics` | ACE-Step `/format_input` → Ollama → built-in writer | api |
| 6 | **Structure tags** `[Verse] [Chorus] [Bridge]` | tag buttons in the lyrics editor | passed through | e2e |
| 7 | **Library** with titles, cover art, status, likes | `/api/songs`, SQLite | n/a | api, e2e |
| 8 | **Player** with waveform and seek | bottom player bar, waveform decoded in the browser (Web Audio) | n/a | e2e |
| 9 | **Extend** a clip | `/api/songs/{id}/extend` | pad + `repaint` the new tail (ACE-Step's documented "continue writing" approach) | api |
| 10 | **Cover / remix** in a new style | `/api/songs/{id}/cover` | `task_type: cover`, `audio_cover_strength` | api |
| 11 | **Replace section** (time range) | `/api/songs/{id}/replace` | `task_type: repaint` | api |
| 12 | **Persona**: reuse a song's style/voice | `/api/personas` | `reference_audio` + saved style | api |
| 13 | **Upload audio** then extend/cover it | `/api/upload` | stored as a library clip | api |
| 14 | **Stems** (vocals / instrumental) | `/api/songs/{id}/stems` | Demucs if installed (optional) | api (skips if absent) |
| 15 | **Advanced**: duration, BPM, key, seed | create form "Advanced" | ACE-Step metadata params | api |
| 16 | **Download** | `/api/songs/{id}/audio?download=1` | n/a | api |
| 17 | Song **detail** view with lyrics, style, parent clip | detail drawer | n/a | e2e |
| 18 | Works **offline / free / unlimited** | local only, no accounts or credits | ACE-Step local or demo synth | api |

Things Suno has that are out of scope for v1: social feed, sharing pages,
user accounts, voice cloning from your own singing ("Voices"), custom model
training UI (ACE-Step supports LoRA training via its own Gradio UI; see
README).
