"""Song planner: turns a one-line description into full song plans with a local LLM.

ACE-Step's built-in 0.6B planner (Simple mode / sample_query) sometimes picks the wrong
language, odd tempos (e.g. 33 BPM for "upbeat") and gives every take the same lyrics.
A bigger local model via Ollama writes one distinct plan per take instead, and ACE-Step
then renders each plan in Custom mode with exact BPM, key and length.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field

import httpx

from .config import settings

SYSTEM = """You are a professional songwriter and record producer planning ONE original song
for an AI music generator. Reply with JSON only, matching this shape:
{"title": str, "style": str, "lyrics": str, "bpm": int, "key": str, "duration": int, "art": str}

Rules:
- title: 1 to 4 evocative words, Title Case, no quotes, not a generic phrase.
- style: a production brief of 3 or 4 sentences (45 to 90 words) written like a record
  producer describing the finished track: genre and era; tempo feel and groove; drums and
  bass; the 2 to 4 lead instruments and their sound; the lead vocal (gender, timbre,
  delivery, harmonies); how the arrangement builds from intro through chorus to the end;
  and the mix character. Plain descriptive prose. No artist names. No BPM or key numbers.
- lyrics: original lyrics in {language}, a complete song of 20 to 32 sung lines, using
  this structure with each section tag alone on its own line in square brackets and a
  blank line between sections:
  [Intro] (no lines), [Verse 1] (4 lines), [Pre-Chorus] (2 lines), [Chorus] (4 lines),
  [Verse 2] (4 lines), [Pre-Chorus] (2 lines), [Chorus] (the same 4 lines again, written
  out in full), [Bridge] (2 to 4 lines), [Chorus] (written out in full), [Outro].
  Short singable lines with rhythm and rhyme, a memorable hook repeated in the chorus,
  concrete images from the description. For an instrumental write only "[Instrumental]".
- bpm: an integer that fits the genre and mood (ballads 60-80, pop 95-125,
  synthwave 95-120, house 120-128, drum and bass 165-175).
- key: like "A minor" or "E major".
- duration: seconds, {duration_rule}.
- art: one sentence describing a striking album cover image for this song: subject,
  setting, color palette and art style. No text, letters, logos or real people.
Never copy existing songs or name real artists."""


# Each take gets its own creative direction so parallel takes come out different.
ANGLES = [
    "",
    "tell it as a story with a specific character and a specific place, and pick a different title.",
    "make it more intimate and personal, with a different title and a different hook.",
    "make it bigger and more anthemic, with a different title and a chant-like hook.",
]


HIGH_VARIETY = [
    "reimagine it in a clearly different genre that still fits the idea, with a new title.",
    "make it stripped back and acoustic, with a new title and a new hook.",
    "flip the mood (if the idea is happy make it bittersweet, and the reverse), with a new title.",
]


@dataclass
class SongPlan:
    title: str
    style: str
    lyrics: str
    bpm: int
    key: str
    duration: int
    art: str = ""
    source: str = "ollama"
    extra: dict = field(default_factory=dict)


def _clean_key(key: str) -> str:
    m = re.match(r"\s*([A-Ga-g])\s*([#b♯♭]?)\s*(major|minor|maj|min|m)?", key or "")
    if not m:
        return ""
    quality = (m.group(3) or "major").lower()
    quality = "minor" if quality in ("minor", "min", "m") else "major"
    acc = m.group(2).replace("♯", "#").replace("♭", "b")
    return f"{m.group(1).upper()}{acc} {quality}"


def _validate(data: dict, *, duration: int | None, bpm: int | None, key: str,
              instrumental: bool) -> SongPlan:
    title = re.sub(r'["“”]', "", str(data.get("title") or "")).strip()[:60] or "Untitled"
    style = str(data.get("style") or "").strip().strip(".")
    lyrics = str(data.get("lyrics") or "").strip()
    if instrumental:
        lyrics = "[Instrumental]"
    if not instrumental:
        sung = [l for l in lyrics.splitlines() if l.strip() and not l.strip().startswith("[")]
        choruses = len(re.findall(r"^\s*\[(chorus|hook)", lyrics, flags=re.I | re.M))
        if len(sung) < 14 or choruses < 2:
            raise ValueError(f"lyrics too thin ({len(sung)} lines, {choruses} choruses)")
    try:
        plan_bpm = int(float(data.get("bpm") or 0))
    except (TypeError, ValueError):
        plan_bpm = 0
    plan_bpm = bpm or (plan_bpm if 50 <= plan_bpm <= 200 else 110)
    try:
        plan_dur = int(float(data.get("duration") or 0))
    except (TypeError, ValueError):
        plan_dur = 0
    plan_dur = int(duration) if duration else (plan_dur if 60 <= plan_dur <= 300 else 180)
    plan_key = _clean_key(key) or _clean_key(str(data.get("key") or "")) or "C major"
    if not style:
        raise ValueError("planner returned no style")
    art = re.sub(r"\s+", " ", str(data.get("art") or "")).strip()[:400]
    return SongPlan(title=title, style=style, lyrics=lyrics, bpm=plan_bpm, key=plan_key,
                    duration=plan_dur, art=art or f"{title}, {style[:160]}")


class OllamaPlanner:
    def __init__(self, url: str | None = None, model: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url = (url or settings.ollama_url or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or settings.ollama_model
        self._transport = transport
        self._resolved: str | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=timeout, transport=self._transport)

    async def resolve_model(self) -> str | None:
        """The configured model if installed, else a sensible installed fallback."""
        if self._resolved:
            return self._resolved
        try:
            async with self._client(4.0) as c:
                r = await c.get("/api/tags")
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return None
        local = [n for n in names if not n.endswith(":cloud") and "embed" not in n and "llava" not in n]
        want = self.model if ":" in self.model else f"{self.model}:latest"
        for cand in (want, self.model, "llama3.1:latest", "qwen2.5:14b", "qwen2.5:7b",
                     "llama3.2:latest", "glm4:latest"):
            if cand in local:
                self._resolved = cand
                return cand
        self._resolved = local[0] if local else None
        return self._resolved

    async def available(self) -> bool:
        return await self.resolve_model() is not None

    async def unload(self) -> None:
        """Free the planner model's VRAM before ACE-Step renders."""
        model = await self.resolve_model()
        if not model:
            return
        try:
            async with self._client(15.0) as c:
                await c.post("/api/generate", json={"model": model, "keep_alive": 0})
        except Exception:
            pass

    async def plan_many(self, description: str, n: int, **kw) -> list[SongPlan]:
        """Plan n takes in parallel (Ollama batches them), then unload the model."""
        try:
            plans = list(await asyncio.gather(*[
                self.plan(description, take=i + 1, final=False, **kw) for i in range(n)]))
        finally:
            await self.unload()
        seen: set[str] = set()
        for p in plans:  # rare duplicate titles: number them rather than re-plan
            base, k = p.title, 2
            while p.title.lower() in seen:
                p.title, k = f"{base} {k}", k + 1
            seen.add(p.title.lower())
        return plans

    async def plan(self, description: str, *, take: int = 1, duration: int | None = None,
                   bpm: int | None = None, key: str = "", instrumental: bool = False,
                   language: str = "English", avoid_titles: list[str] | None = None,
                   style_hint: str = "", final: bool = True, vocal_gender: str = "",
                   weirdness: int = 50, style_influence: int = 50, variety: str = "normal",
                   exclude: str = "") -> SongPlan:
        model = await self.resolve_model()
        if not model:
            raise RuntimeError("no Ollama model available")
        duration_rule = f"exactly {int(duration)}" if duration else "between 150 and 240"
        system = SYSTEM.replace("{language}", language).replace("{duration_rule}", duration_rule)
        user = f"Song idea: {description.strip()}\n"
        if style_hint:
            if style_influence >= 70:
                user += f"Sound (follow this closely, do not change the genre): {style_hint}\n"
            elif style_influence <= 30:
                user += f"Loose inspiration for the sound, feel free to reinterpret: {style_hint}\n"
            else:
                user += f"Sound to aim for: {style_hint}\n"
        if vocal_gender in ("male", "female") and not instrumental:
            user += f"Lead vocal: {vocal_gender}.\n"
        if exclude.strip():
            user += f"Avoid these entirely (instruments, genres, moods): {exclude.strip()}.\n"
        if weirdness >= 70:
            user += ("Be adventurous: unexpected genre blends, unusual instruments and "
                     "surprising imagery, while staying musical.\n")
        elif weirdness <= 30:
            user += "Keep it classic and expected for the genre, no experiments.\n"
        if instrumental:
            user += "This is an instrumental with no vocals.\n"
        if bpm:
            user += f"Tempo: {bpm} BPM.\n"
        if key:
            user += f"Key: {key}.\n"
        angles = ANGLES if variety != "low" else ["", "same idea and genre, new words and a new title."]
        if variety == "high":
            angles = ["", *HIGH_VARIETY]
        angle = angles[(take - 1) % len(angles)]
        if angle:
            user += f"Creative direction for this take: {angle}\n"
        if avoid_titles:
            user += ("This is an alternate take. Use a different title, a different angle on "
                     f"the idea and new lyrics. Do not reuse: {', '.join(avoid_titles)}.\n")
        body = {
            "model": model,
            "stream": False,
            "format": "json",
            # Keep the model loaded between takes, then free the GPU for ACE-Step.
            "keep_alive": 0 if final else "2m",
            "options": {"temperature": round(0.6 + 0.6 * max(0, min(100, weirdness)) / 100 + 0.05 * (take - 1), 3),
                        "seed": random.randint(1, 2**31 - 1),
                        "num_ctx": 4096, "num_predict": 1200},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        last_err: Exception | None = None
        for _ in range(3):  # retry malformed or too-thin output
            try:
                async with self._client(240.0) as c:
                    r = await c.post("/api/chat", json=body)
                    r.raise_for_status()
                    text = r.json().get("message", {}).get("content", "")
                data = json.loads(text)
                plan = _validate(data, duration=duration, bpm=bpm, key=key, instrumental=instrumental)
                plan.extra["planner_model"] = model
                return plan
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                body["options"]["seed"] = random.randint(1, 2**31 - 1)
        raise RuntimeError(f"planner failed: {last_err}")

    # ------------------------------------------------------------ extras
    async def _ask_json(self, model: str, system: str, user: str, images: list[str] | None = None,
                        timeout: float = 180.0) -> dict:
        msg: dict = {"role": "user", "content": user}
        if images:
            msg["images"] = images
        body = {"model": model, "stream": False, "format": "json", "keep_alive": 0,
                "options": {"temperature": 0.8, "num_ctx": 4096, "num_predict": 400},
                "messages": [{"role": "system", "content": system}, msg]}
        async with self._client(timeout) as c:
            r = await c.post("/api/chat", json=body)
            r.raise_for_status()
            return json.loads(r.json().get("message", {}).get("content", "") or "{}")

    async def art_prompt(self, title: str, style: str, lyrics: str) -> str:
        """One-sentence album cover description for a song that was not planned here."""
        fallback = f"{title}, mood of {style[:200]}" if title else style[:240]
        model = await self.resolve_model()
        if not model:
            return fallback
        sung = " / ".join(l.strip() for l in lyrics.splitlines()
                          if l.strip() and not l.strip().startswith("["))[:600]
        try:
            data = await self._ask_json(
                model, ART_SYSTEM, f"Title: {title}\nSound: {style[:500]}\nLyrics: {sung or '(instrumental)'}",
                timeout=90.0)
            art = re.sub(r"\s+", " ", str(data.get("art") or "")).strip()
            return art[:400] or fallback
        except Exception:
            return fallback

    async def vision_model(self) -> str | None:
        import time as _t

        cached = getattr(self, "_vision", None)
        if cached and _t.monotonic() - cached[0] < 30:
            return cached[1]
        found = await self._find_vision()
        self._vision = (_t.monotonic(), found)
        return found

    async def _find_vision(self) -> str | None:
        try:
            async with self._client(4.0) as c:
                r = await c.get("/api/tags")
                names = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return None
        want = settings.vision_model
        for cand in (want, f"{want}:latest", "llava:7b", "llava:latest", "llama3.2-vision:latest",
                     "minicpm-v:latest", "moondream:latest", "gemma3:4b", "qwen2.5vl:7b"):
            if cand in names:
                return cand
        return None

    async def inspire(self, image_b64: str, note: str = "") -> dict:
        """Turn a picture into a song idea: {description, style, title}."""
        model = await self.vision_model()
        if not model:
            raise RuntimeError("No vision model in Ollama. Install one with: ollama pull llava:7b")
        user = "Write a song idea inspired by this picture."
        if note.strip():
            user += f" The person adds: {note.strip()}"
        data = await self._ask_json(model, INSPIRE_SYSTEM, user, images=[image_b64], timeout=300.0)
        desc = re.sub(r"\s+", " ", str(data.get("description") or "")).strip()[:500]
        if not desc:
            raise RuntimeError("the vision model did not return an idea")
        style = data.get("style") or ""
        if isinstance(style, (list, tuple)):  # small vision models often return a list
            style = ", ".join(str(x) for x in style)
        style = re.sub(r"[\[\]'\"]", "", str(style))
        return {"description": desc,
                "style": re.sub(r"\s+", " ", style).strip(" ,")[:300],
                "title": re.sub(r"\s+", " ", str(data.get("title") or "")).strip()[:80],
                "model": model}


ART_SYSTEM = """You design album covers. Reply with JSON {"art": str}: one vivid sentence
describing a cover image for the song: subject, setting, color palette and art style
(photo, painting, illustration, 3D, collage...). No text, letters, logos or real people."""

INSPIRE_SYSTEM = """You turn pictures into song ideas. Look at the image: mood, place,
colors, story. Reply with JSON {"title": str, "description": str, "style": str}:
description is one or two sentences saying what the song is about and how it feels
(like a request to a songwriter); style is a short list of genre, instruments and vocal
type that fits the picture. Never name real artists."""
