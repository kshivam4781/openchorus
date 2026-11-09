"""Lyrics and title helpers. Lyrics come from ACE-Step's LM, then Ollama, then a built-in writer."""
from __future__ import annotations

import re

import httpx

from .config import settings
from .engines.base import Engine
from .engines.demo import DemoEngine

SYSTEM = (
    "You write original song lyrics. Use section tags on their own line in square brackets: "
    "[Verse 1], [Chorus], [Verse 2], [Bridge], [Outro]. Keep lines short and singable. "
    "Never copy existing songs. Output only the lyrics."
)


async def _ollama(prompt: str, style: str) -> str | None:
    if not settings.ollama_url or settings.planner == "acestep":
        return None
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{settings.ollama_url}/api/generate", json={
                "model": settings.ollama_model, "system": SYSTEM, "stream": False,
                "prompt": f"Song idea: {prompt}\nStyle: {style or 'any'}\nWrite the lyrics.",
            })
            r.raise_for_status()
            text = r.json().get("response", "").strip()
    except Exception:
        return None
    return text if "[" in text and len(text.splitlines()) >= 6 else None


async def write_lyrics(engine: Engine, prompt: str, style: str = "") -> dict:
    from .planner import OllamaPlanner

    if settings.planner != "acestep":
        planner = OllamaPlanner()
        if prompt.strip() and await planner.available():
            try:
                plan = await planner.plan(prompt, style_hint=style)
                return {"lyrics": plan.lyrics, "style": style or plan.style, "title": plan.title,
                        "source": "ollama"}
            except Exception:
                pass
    res = await engine.write_lyrics(prompt, style)
    if res and res.get("lyrics") and not isinstance(engine, DemoEngine):
        return {**res, "source": engine.name}
    text = await _ollama(prompt, style)
    if text:
        return {"lyrics": text, "style": style, "source": "ollama"}
    res = await DemoEngine().write_lyrics(prompt, style)
    return {**res, "source": "built-in"}


def make_title(lyrics: str = "", description: str = "", style: str = "") -> str:
    lines = [l.strip() for l in (lyrics or "").splitlines()]
    pick = ""
    for i, line in enumerate(lines):
        if re.match(r"\[(chorus|hook)", line, re.I):
            pick = next((l for l in lines[i + 1:] if l and not l.startswith("[")), "")
            break
    if not pick:
        pick = next((l for l in lines if l and not l.startswith("[")), "")
    if not pick:
        pick = description or style or "Untitled"
    # Prefer the first phrase of the line ("So let it ring, let it ring" -> "So let it ring").
    phrase = re.split(r"[,;:.!?(—–]", pick)[0]
    if len(phrase.split()) >= 2:
        pick = phrase
    words = re.sub(r"[^\w\s'&-]", "", pick).split()
    title = " ".join(words if len(words) <= 7 else words[:5]) or "Untitled"
    return title[:1].upper() + title[1:]
