"""Offline demo engine: a small procedural synthesizer.

It is NOT an AI model and cannot sing. It exists so the whole app (library,
player, extend, cover, replace section, personas) can be used and tested on
any machine, with no GPU and no downloads. It turns the style text and the
lyric structure tags into an arrangement: drums, bass, chords, and a lead line
that stands in for the vocal melody.
"""
from __future__ import annotations

import asyncio
import math
import random
import re

import numpy as np
from scipy.signal import butter, lfilter

from .. import audio as A
from .base import Engine, GenRequest, GenResult, Progress

SR = A.SAMPLE_RATE
NOTE_INDEX = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
              "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]

SECTION_ENERGY = {
    "intro": 0.35, "verse": 0.65, "pre-chorus": 0.8, "prechorus": 0.8,
    "chorus": 1.0, "hook": 1.0, "drop": 1.0, "bridge": 0.55, "breakdown": 0.4,
    "build": 0.8, "instrumental": 0.85, "solo": 0.9, "outro": 0.4, "interlude": 0.5,
}

TEMPO_HINTS = [
    (("drum and bass", "dnb", "jungle"), 172), (("dubstep",), 140), (("trap",), 140),
    (("techno",), 130), (("house", "edm", "dance", "disco"), 124), (("punk",), 170),
    (("metal",), 150), (("rock",), 122), (("synthwave", "retrowave", "80s"), 108),
    (("funk",), 108), (("pop",), 116), (("reggae", "reggaeton"), 92),
    (("hip hop", "hip-hop", "rap", "boom bap"), 90), (("r&b", "rnb", "soul"), 84),
    (("jazz", "swing"), 118), (("lofi", "lo-fi", "chill"), 78), (("ballad",), 70),
    (("ambient", "cinematic", "orchestral"), 72), (("folk", "acoustic", "country"), 100),
]

MINOR_WORDS = ("sad", "dark", "melanchol", "minor", "moody", "haunting", "lonely",
               "heartbreak", "blue", "night", "trap", "drill", "metal", "cinematic")


def parse_key(key_scale: str) -> tuple[int, bool] | None:
    m = re.match(r"\s*([A-Ga-g])([#b♯♭]?)\s*(m(?!aj)|min|minor|maj|major)?", key_scale or "")
    if not m:
        return None
    name = (m.group(1) + m.group(2).replace("♯", "#").replace("♭", "b")).upper()
    if name not in NOTE_INDEX:
        return None
    quality = (m.group(3) or "").lower()
    minor = quality in ("m", "min", "minor")
    return NOTE_INDEX[name], minor


def key_name(root: int, minor: bool) -> str:
    return f"{NOTE_NAMES[root]} {'minor' if minor else 'major'}"


def style_tempo(style: str) -> int | None:
    s = style.lower()
    for words, bpm in TEMPO_HINTS:
        if any(w in s for w in words):
            return bpm
    return None


def parse_sections(lyrics: str) -> list[str]:
    tags = re.findall(r"^\s*\[([^\]]+)\]", lyrics or "", flags=re.M)
    out = []
    for t in tags:
        base = t.split("-")[0].split(":")[0].strip().lower()
        base = re.sub(r"\s*\d+$", "", base)
        if base:
            out.append(base)
    return out or ["intro", "verse", "chorus", "verse", "chorus", "bridge", "chorus", "outro"]


def _lp(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    b, a = butter(order, min(cutoff, SR * 0.45) / (SR / 2), btype="low")
    return lfilter(b, a, x).astype(np.float32)


def _hp(x: np.ndarray, cutoff: float) -> np.ndarray:
    b, a = butter(2, cutoff / (SR / 2), btype="high")
    return lfilter(b, a, x).astype(np.float32)


def _env(n: int, attack: float, release: float, sustain: float = 1.0, decay: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    env = np.ones(n, np.float32) * sustain
    if decay > 0:
        env = sustain + (1 - sustain) * np.exp(-t / decay)
    a = max(1, int(attack * SR))
    env[:a] *= np.linspace(0, 1, min(a, n), dtype=np.float32)[: len(env[:a])]
    r = max(1, int(release * SR))
    if r < n:
        env[-r:] *= np.linspace(1, 0, r, dtype=np.float32)
    return env.astype(np.float32)


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


class Palette:
    """Instrument choices derived from the style text."""

    def __init__(self, style: str):
        s = style.lower()
        self.drums = not any(w in s for w in ("ambient", "classical", "orchestral", "a cappella", "no drums"))
        self.soft_drums = any(w in s for w in ("acoustic", "folk", "ballad", "lofi", "lo-fi", "jazz", "piano"))
        self.four_on_floor = any(w in s for w in ("house", "edm", "dance", "disco", "techno", "synthwave"))
        self.half_time = any(w in s for w in ("trap", "hip hop", "hip-hop", "rap", "lofi", "lo-fi", "boom bap"))
        self.swing = any(w in s for w in ("jazz", "swing", "lofi", "lo-fi", "boom bap"))
        if any(w in s for w in ("rock", "metal", "punk", "grunge")):
            self.chords = "guitar"
        elif any(w in s for w in ("piano", "ballad", "classical")):
            self.chords = "piano"
        elif any(w in s for w in ("jazz", "lofi", "lo-fi", "soul", "r&b", "rnb", "neo")):
            self.chords = "epiano"
        elif any(w in s for w in ("orchestral", "cinematic", "strings", "epic", "ambient")):
            self.chords = "strings"
        else:
            self.chords = "saw"
        self.lofi = any(w in s for w in ("lofi", "lo-fi", "vinyl", "tape"))
        self.bright = any(w in s for w in ("bright", "happy", "upbeat", "energetic", "summer", "edm"))


class Synth:
    def __init__(self, seed: int):
        self.seed = seed

    # ---- instruments ----------------------------------------------------
    def kick(self, n: int) -> np.ndarray:
        t = np.arange(n) / SR
        f = 45 + 110 * np.exp(-t * 28)
        ph = 2 * np.pi * np.cumsum(f) / SR
        return (np.sin(ph) * np.exp(-t * 7)).astype(np.float32)

    def snare(self, n: int, rng: np.random.Generator) -> np.ndarray:
        t = np.arange(n) / SR
        noise = _hp(rng.standard_normal(n).astype(np.float32), 1200) * np.exp(-t * 18)
        body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 25)
        return (0.7 * noise + 0.5 * body).astype(np.float32)

    def hat(self, n: int, rng: np.random.Generator, open_: bool = False) -> np.ndarray:
        t = np.arange(n) / SR
        return (_hp(rng.standard_normal(n).astype(np.float32), 7000) * np.exp(-t * (12 if open_ else 55))).astype(np.float32)

    def tone(self, midi: float, n: int, kind: str, vel: float = 1.0) -> np.ndarray:
        t = np.arange(n) / SR
        f = _hz(midi)
        if kind == "saw":
            sig = sum(np.sin(2 * np.pi * f * d * t * k) / k for d in (0.997, 1.003) for k in range(1, 9)) * 0.5
            sig = _lp(sig.astype(np.float32), 2600) * _env(n, 0.04, 0.12, 0.85)
        elif kind == "strings":
            sig = sum(np.sin(2 * np.pi * f * d * t * k) / k for d in (0.995, 1.0, 1.005) for k in range(1, 7)) * 0.35
            sig = _lp(sig.astype(np.float32), 1800) * _env(n, 0.35, 0.4, 0.9)
        elif kind == "piano":
            sig = (np.sin(2 * np.pi * f * t) + 0.45 * np.sin(4 * np.pi * f * t) * np.exp(-t * 3)
                   + 0.2 * np.sin(6 * np.pi * f * t) * np.exp(-t * 5))
            sig = sig * _env(n, 0.003, 0.08, 0.0, decay=1.1)
        elif kind == "epiano":
            mod = np.sin(2 * np.pi * f * t) * 1.2 * np.exp(-t * 4)
            sig = np.sin(2 * np.pi * f * t + mod) * _env(n, 0.004, 0.1, 0.15, decay=0.9)
        elif kind == "guitar":
            raw = sum(np.sin(2 * np.pi * f * t * k) / k for k in range(1, 7))
            sig = np.tanh(raw * 3.5) * 0.6
            sig = _lp(sig.astype(np.float32), 3200) * _env(n, 0.005, 0.08, 0.7, decay=0.6)
        elif kind == "bass":
            sig = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
            sig = np.tanh(sig * 1.4) * _env(n, 0.005, 0.05, 0.8, decay=0.4)
        elif kind == "vox":
            vib = 0.35 * np.sin(2 * np.pi * 5.2 * t) * np.clip(t * 2.5, 0, 1)
            ff = f * 2 ** (vib / 12)
            ph = 2 * np.pi * np.cumsum(ff) / SR
            sig = np.sin(ph) + 0.35 * np.sin(2 * ph) + 0.18 * np.sin(3 * ph) + 0.08 * np.sin(5 * ph)
            sig = _lp(sig.astype(np.float32), 3500) * _env(n, 0.06, 0.12, 0.9)
        else:  # lead
            sig = np.sign(np.sin(2 * np.pi * f * t)) * 0.5 + 0.5 * np.sin(2 * np.pi * f * 2.001 * t)
            sig = _lp(sig.astype(np.float32), 2400) * _env(n, 0.01, 0.08, 0.75)
        return (np.asarray(sig, np.float32) * vel)

    # ---- arrangement ----------------------------------------------------
    def render(self, *, style: str, lyrics: str, duration: float, bpm: int, root: int,
               minor: bool, instrumental: bool, melody_seed: int | None = None) -> np.ndarray:
        rng = np.random.default_rng(self.seed)
        mrng = random.Random(melody_seed if melody_seed is not None else self.seed)
        pal = Palette(style)
        beat = 60.0 / bpm
        bar = 4 * beat
        n_total = int(duration * SR)
        n_bars = max(1, math.ceil(duration / bar))

        sections = parse_sections(lyrics)
        plan: list[str] = []
        si = 0
        while len(plan) < n_bars:
            sec = sections[si % len(sections)]
            length = 2 if sec in ("intro", "outro", "build", "breakdown") else 4
            plan += [sec] * length
            si += 1
        plan = plan[:n_bars]
        if n_bars >= 4:
            plan[-1] = "outro"

        scale = MINOR if minor else MAJOR
        prog_degrees = [[0, 5, 2, 6], [0, 3, 5, 4], [5, 3, 0, 4]][self.seed % 3] if minor else \
                       [[0, 4, 5, 3], [0, 5, 3, 4], [3, 4, 0, 5]][self.seed % 3]
        base = 48 + root  # C3 + root

        def chord(deg: int) -> list[int]:
            notes = []
            for step in (0, 2, 4):
                d = deg + step
                notes.append(base + scale[d % 7] + 12 * (d // 7))
            return notes

        # one remembered melody per section type, so choruses repeat
        melodies: dict[str, list[tuple[float, int | None]]] = {}

        def melody_for(sec: str) -> list[tuple[float, int | None]]:
            if sec not in melodies:
                notes = []
                high = sec in ("chorus", "hook", "drop")
                for _ in range(8):
                    if mrng.random() < (0.18 if high else 0.3):
                        notes.append((0.5, None))
                        continue
                    deg = mrng.choice([0, 2, 4, 4, 2, 1, 3, 5, 6]) + (7 if high else 0)
                    length = mrng.choice([0.5, 0.5, 0.5, 1.0])
                    notes.append((length, deg))
                melodies[sec] = notes
            return melodies[sec]

        L = np.zeros(n_total + SR * 3, np.float32)
        R = np.zeros_like(L)

        def add(sig: np.ndarray, start_s: float, gain: float, pan: float = 0.0) -> None:
            i = int(start_s * SR)
            if i >= len(L):
                return
            sig = sig[: len(L) - i]
            lg = gain * math.cos((pan + 1) * math.pi / 4)
            rg = gain * math.sin((pan + 1) * math.pi / 4)
            L[i:i + len(sig)] += sig * lg
            R[i:i + len(sig)] += sig * rg

        kick_s, snare_s = self.kick(int(0.45 * SR)), self.snare(int(0.3 * SR), rng)
        hat_c, hat_o = self.hat(int(0.08 * SR), rng), self.hat(int(0.35 * SR), rng, True)
        swing = 0.12 * beat if pal.swing else 0.0
        drum_gain = 0.55 if pal.soft_drums else 0.8

        for b, sec in enumerate(plan):
            t0 = b * bar
            energy = SECTION_ENERGY.get(sec, 0.7)
            deg = prog_degrees[b % 4]
            notes = chord(deg)
            # chords
            if pal.chords in ("piano", "epiano", "guitar"):
                hits = [0, 2] if pal.chords != "guitar" or energy < 0.7 else [0, 1, 2, 3]
                for h in hits:
                    for k, nt in enumerate(notes):
                        add(self.tone(nt + 12, int(beat * (4 / len(hits)) * SR * 1.1), pal.chords, 0.5),
                            t0 + h * beat + k * 0.012, 0.32 * (0.6 + 0.4 * energy), pan=(k - 1) * 0.4)
            else:
                for k, nt in enumerate(notes):
                    add(self.tone(nt + 12, int(bar * SR), pal.chords, 0.5), t0, 0.26 * (0.5 + 0.5 * energy),
                        pan=(k - 1) * 0.6)
            # bass
            if sec != "intro" or energy > 0.5:
                pattern = [0, 1.5, 2, 3.5] if energy >= 0.8 else [0, 2]
                for p in pattern:
                    add(self.tone(notes[0] - 12, int(beat * 1.4 * SR), "bass"), t0 + p * beat, 0.5 * energy)
            # drums
            if pal.drums and sec not in ("intro", "breakdown") and energy >= 0.5:
                for q in range(4):
                    bt = t0 + q * beat
                    if pal.four_on_floor or (q in (0, 2) and not pal.half_time) or (pal.half_time and q == 0):
                        add(kick_s, bt, drum_gain)
                    if pal.half_time and q == 2:
                        add(snare_s, bt, drum_gain * 0.8)
                    elif not pal.half_time and q in (1, 3):
                        add(snare_s, bt, drum_gain * 0.75)
                    for e in (0, 1):
                        off = e * (beat / 2 + swing)
                        add(hat_o if (pal.four_on_floor and e == 1) else hat_c, bt + off,
                            0.18 * energy, pan=0.35)
            # melody (stand-in for the vocal line)
            if sec not in ("intro", "outro", "breakdown") and (energy >= 0.6 or sec == "bridge"):
                kind = "lead" if instrumental else "vox"
                t = t0
                for length, mdeg in melody_for(sec):
                    if mdeg is not None:
                        midi = base + 12 + scale[mdeg % 7] + 12 * (mdeg // 7)
                        add(self.tone(midi, int(length * beat * SR * 0.95), kind), t, 0.3)
                    t += length * beat

        mix = np.stack([L, R], axis=1)[:n_total]
        if pal.lofi:
            mix = np.stack([_lp(mix[:, 0], 5000), _lp(mix[:, 1], 5000)], axis=1)
            crackle = (rng.random(len(mix)) > 0.9993).astype(np.float32) * rng.standard_normal(len(mix)).astype(np.float32)
            mix += (crackle * 0.08 + rng.standard_normal(len(mix)).astype(np.float32) * 0.004)[:, None]
        mix = np.tanh(mix * 1.2)
        fade = min(len(mix), int(1.5 * SR))
        mix[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        mix[: int(0.01 * SR)] *= np.linspace(0, 1, int(0.01 * SR), dtype=np.float32)[:, None]
        peak = float(np.max(np.abs(mix))) or 1.0
        return (mix / peak * 0.89).astype(np.float32)


DEMO_LYRIC_LINES = {
    "verse": [
        "Streetlights hum a song I almost know", "Coffee's cold, the city's moving slow",
        "Every road I take still leads me back to you", "Counting stars like they could tell me what to do",
        "Paper planes are drifting through the rain", "I keep your laugh inside my window pane",
        "Woke up early chasing something new", "The sky was gold, the morning felt like proof",
    ],
    "chorus": [
        "So let it ring, let it ring tonight", "We were made to run into the light",
        "Hold on, hold on, don't let go", "This is the part where the heartbeat shows",
    ],
    "bridge": [
        "And if the world goes quiet, I'll still hear you", "Every ending is a door we walk through",
    ],
}


def demo_lyrics(prompt: str, seed: int) -> str:
    rng = random.Random(seed)
    subject = (prompt or "").strip().rstrip(".")
    v1 = rng.sample(DEMO_LYRIC_LINES["verse"], 4)
    v2 = rng.sample(DEMO_LYRIC_LINES["verse"], 4)
    ch = DEMO_LYRIC_LINES["chorus"][:]
    rng.shuffle(ch)
    br = DEMO_LYRIC_LINES["bridge"]
    if subject:
        v1[0] = f"{subject[:1].upper()}{subject[1:60]}"
    return "\n".join([
        "[Verse 1]", *v1, "", "[Chorus]", *ch, "", "[Verse 2]", *v2, "",
        "[Chorus]", *ch, "", "[Bridge]", *br, "", "[Chorus]", *ch[:2], "", "[Outro]",
    ])


DEMO_IDEAS = [
    {"description": "an upbeat synthwave song about driving through a neon city at 2am",
     "style": "synthwave, retro 80s, driving bass, bright synth leads, male vocals"},
    {"description": "a cozy lo-fi track for studying on a rainy evening",
     "style": "lo-fi hip hop, mellow electric piano, vinyl crackle, chill"},
    {"description": "an anthemic pop rock chorus about never giving up",
     "style": "pop rock, anthemic, electric guitars, big drums, female vocals"},
    {"description": "a soft piano ballad about missing home",
     "style": "piano ballad, emotional, intimate vocals, strings"},
    {"description": "a dark cinematic trap beat",
     "style": "dark trap, 808 bass, cinematic strings, moody"},
]


class DemoEngine(Engine):
    name = "demo"
    label = "Demo synth (offline, no AI vocals)"

    async def generate(self, req: GenRequest, progress: Progress) -> list[GenResult]:
        results: list[GenResult] = []
        n = max(1, min(4, req.variations))
        for i in range(n):
            await progress(f"Synthesizing variation {i + 1} of {n}")
            results.append(await asyncio.to_thread(self._one, req, i))
        return results

    def _one(self, req: GenRequest, idx: int) -> GenResult:
        pp = req.parent_params or {}
        base_seed = req.seed if req.seed is not None else random.randint(1, 2**31 - 1)
        seed = (base_seed + idx * 7919) % (2**31 - 1)
        style = req.style or req.description or "pop"
        lyrics = req.lyrics
        if req.mode == "simple" and not req.instrumental and not lyrics:
            lyrics = demo_lyrics(req.description, seed)
        if req.instrumental and not lyrics:
            lyrics = "[Instrumental]"

        parsed = parse_key(req.key_scale) if req.key_scale else None
        if req.task in ("cover", "extend", "replace") and pp:
            parsed = parsed or parse_key(pp.get("key_scale", ""))
        if parsed is None:
            r = random.Random(seed)
            minor = any(w in style.lower() for w in MINOR_WORDS) or r.random() < 0.3
            parsed = (r.randrange(12), minor)
        root, minor = parsed
        bpm = req.bpm or (pp.get("bpm") if req.task != "create" else None) or style_tempo(style) or 110
        bpm = int(max(50, min(200, bpm)))
        melody_seed = pp.get("melody_seed") if req.task in ("cover", "extend") else None
        if melody_seed is None:
            melody_seed = seed

        out_wav = req.out_dir / f"{req.name_prefix}_{idx}.wav"
        if req.task == "create" or req.task == "cover":
            dur = float(req.duration or pp.get("duration") or 30)
            mix = Synth(seed).render(style=style, lyrics=lyrics, duration=dur, bpm=bpm, root=root,
                                     minor=minor, instrumental=req.instrumental, melody_seed=melody_seed)
        elif req.task == "extend":
            src = A.decode_to_array(req.src_audio)  # type: ignore[arg-type]
            extra = max(5.0, (req.repaint_end or 0) - (req.repaint_start or 0))
            tail = Synth(seed).render(style=style, lyrics=lyrics or pp.get("lyrics", ""), duration=extra + 1.0,
                                      bpm=bpm, root=root, minor=minor, instrumental=req.instrumental,
                                      melody_seed=melody_seed)
            xf = int(1.0 * SR)
            head = src[: max(xf, len(src) - xf // 2)]
            ramp = np.linspace(0, 1, xf, dtype=np.float32)[:, None]
            joined = head.copy()
            joined[-xf:] = head[-xf:] * (1 - ramp) + tail[:xf] * ramp
            mix = np.vstack([joined, tail[xf:]])
        elif req.task == "replace":
            src = A.decode_to_array(req.src_audio)  # type: ignore[arg-type]
            s = int(req.repaint_start * SR)
            e = min(len(src), int(req.repaint_end * SR))
            if e - s < SR:
                raise ValueError("section must be at least 1 second long")
            new = Synth(seed).render(style=style, lyrics=lyrics, duration=(e - s) / SR + 0.5, bpm=bpm,
                                     root=root, minor=minor, instrumental=req.instrumental)
            mix = src.copy()
            xf = min(int(0.3 * SR), (e - s) // 4)
            seg = new[: e - s].copy()
            ramp = np.linspace(0, 1, xf, dtype=np.float32)[:, None]
            seg[:xf] = src[s:s + xf] * (1 - ramp) + seg[:xf] * ramp
            seg[-xf:] = seg[-xf:] * (1 - ramp) + src[e - xf:e] * ramp
            mix[s:e] = seg
        else:
            raise ValueError(f"unsupported task {req.task}")

        A.write_wav(out_wav, mix)
        final = A.to_mp3(out_wav, out_wav.with_suffix(".mp3"))
        return GenResult(
            file=final, duration=round(len(mix) / SR, 2), style=style, lyrics=lyrics, bpm=bpm,
            key_scale=key_name(root, minor), time_signature="4", seed=seed,
            extra={"melody_seed": melody_seed},
        )

    async def write_lyrics(self, prompt: str, style: str = "") -> dict:
        return {"lyrics": demo_lyrics(prompt, random.randint(1, 10**6)), "style": style}

    async def random_idea(self) -> dict:
        return random.choice(DEMO_IDEAS)
