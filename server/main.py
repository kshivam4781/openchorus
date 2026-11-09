"""OpenChorus API server and web app."""
from __future__ import annotations

import asyncio
import random
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import audio as A
from . import stems as S
from .config import ROOT, settings
from .db import Store
from .engines import EngineError, EngineManager, GenRequest, GenResult
from .lyrics import make_title, write_lyrics
from .planner import OllamaPlanner
from .auxclient import AuxClient

ALLOWED_UPLOAD = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


class State:
    store: Store
    engines: EngineManager
    gpu_lock: asyncio.Semaphore
    progress: dict[str, str]
    tasks: set[asyncio.Task]
    planner: OllamaPlanner
    aux: AuxClient
    video_lock: asyncio.Lock


state = State()


def init_state() -> None:
    settings.ensure_dirs()
    state.store = Store(settings.db_path)
    state.engines = EngineManager()
    state.gpu_lock = asyncio.Semaphore(1)
    state.progress = {}
    state.tasks = set()
    state.planner = OllamaPlanner()
    state.aux = AuxClient()
    state.video_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_state()
    resume_finishing()
    yield


def resume_finishing() -> None:
    """Songs that were being mastered, rated or painted when the server stopped would show
    "Mastering" forever. Finish that work in the background instead."""
    for song in state.store.list_songs(limit=100_000):
        p = song.get("params") or {}
        if song.get("status") != "complete":
            continue
        if p.get("polishing"):
            spawn(polish(song["id"], scored=bool(p.get("rating"))))
        if p.get("art_status") in ("queued", "painting"):
            async def paint(sid: str = song["id"]) -> None:
                async with state.gpu_lock:
                    try:
                        if not await make_art(sid):
                            merge_params(sid, art_status=None)
                    except Exception as exc:  # noqa: BLE001
                        merge_params(sid, art_status="failed", art_error=str(exc)[:200])
            spawn(paint())


app = FastAPI(title="OpenChorus", version=__version__, lifespan=lifespan)


# ---------------------------------------------------------------- models
class GenerateBody(BaseModel):
    mode: Literal["simple", "custom"] = "custom"
    description: str = Field("", max_length=1000)
    title: str = Field("", max_length=120)
    style: str = Field("", max_length=1000)
    lyrics: str = Field("", max_length=6000)
    instrumental: bool = False
    variations: int = Field(2, ge=1, le=4)
    duration: Optional[float] = Field(None, ge=10, le=600)
    bpm: Optional[int] = Field(None, ge=30, le=300)
    key_scale: str = Field("", max_length=20)
    time_signature: str = Field("", max_length=4)
    seed: Optional[int] = Field(None, ge=0)
    vocal_language: str = Field("en", max_length=8)
    persona_id: Optional[str] = None
    quality: Literal["high", "fast"] = "high"
    kind: Literal["song", "sound"] = "song"
    vocal_gender: Literal["", "male", "female"] = ""
    weirdness: int = Field(50, ge=0, le=100)
    style_influence: int = Field(50, ge=0, le=100)
    variety: Literal["low", "normal", "high"] = "normal"
    exclude_styles: str = Field("", max_length=300)
    max_mode: bool = False


class ExtendBody(BaseModel):
    seconds: float = Field(30, ge=5, le=90)
    style: Optional[str] = None
    lyrics: Optional[str] = None
    variations: int = Field(1, ge=1, le=4)


class CoverBody(BaseModel):
    style: str = Field(..., min_length=1, max_length=1000)
    lyrics: Optional[str] = None
    strength: float = Field(0.6, ge=0.0, le=1.0)
    variations: int = Field(2, ge=1, le=4)
    title: str = ""


class ReplaceBody(BaseModel):
    start: float = Field(..., ge=0)
    end: float = Field(..., gt=0)
    lyrics: Optional[str] = None
    style: Optional[str] = None
    variations: int = Field(1, ge=1, le=4)


class SongPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    liked: Optional[bool] = None
    disliked: Optional[bool] = None


class EditBody(BaseModel):
    op: Literal["crop", "remove", "reverse", "speed", "fade_in", "fade_out"]
    start: float = Field(0, ge=0)
    end: float = Field(0, ge=0)
    factor: float = Field(1.0, ge=0.5, le=2.0)
    seconds: float = Field(3.0, ge=0.5, le=30)


class CoverArtBody(BaseModel):
    prompt: str = Field("", max_length=1200)


class StemsBody(BaseModel):
    stems: int = Field(4, ge=2, le=4)


class PersonaBody(BaseModel):
    song_id: str
    name: str = Field("", max_length=60)


class LyricsBody(BaseModel):
    prompt: str = Field("", max_length=1000)
    style: str = Field("", max_length=1000)


# --------------------------------------------------------------- helpers
def public_song(song: dict[str, Any]) -> dict[str, Any]:
    out = dict(song)
    out["audio_url"] = f"/api/songs/{song['id']}/audio" if song.get("audio_file") else None
    # Versioned URL for playback, so a take that gets mastered mid-play keeps streaming the same file.
    out["stream_url"] = f"/api/songs/{song['id']}/audio?v={song['audio_file']}" if song.get("audio_file") else None
    out["art_url"] = f"/api/songs/{song['id']}/art?v={song['cover_file']}" if song.get("cover_file") else None
    out["has_wav"] = bool((song.get("params") or {}).get("wav_file"))
    out["progress"] = state.progress.get(song.get("batch_id") or "", "") if song["status"] in ("queued", "running") else ""
    return out


def get_song_or_404(song_id: str) -> dict[str, Any]:
    song = state.store.get_song(song_id)
    if not song:
        raise HTTPException(404, "song not found")
    return song


def require_audio(song: dict[str, Any]) -> Path:
    if song["status"] != "complete" or not song.get("audio_file"):
        raise HTTPException(409, "song has no finished audio yet")
    path = settings.audio_dir / song["audio_file"]
    if not path.exists():
        raise HTTPException(410, "audio file is missing on disk")
    return path


def spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)


def merge_params(song_id: str, **kv: Any) -> dict | None:
    """Read-modify-write a song's params with no await in between (safe across tasks)."""
    song = state.store.get_song(song_id)
    if not song:
        return None
    params = dict(song.get("params") or {})
    params.update({k: v for k, v in kv.items()})
    return state.store.update_song(song_id, params=params)


async def polish(song_id: str, scored: bool) -> None:
    """After a take lands: master it to streaming loudness, then rate it with the judge."""
    song = state.store.get_song(song_id)
    if not song or not song.get("audio_file"):
        return
    raw = settings.audio_dir / song["audio_file"]
    already = bool((song.get("params") or {}).get("wav_file"))
    if settings.mastering and A.has_ffmpeg() and raw.exists() and not already:
        merge_params(song_id, polishing="mastering")
        try:
            out = await asyncio.to_thread(A.master, raw, raw.with_name(raw.stem + "_mx"), settings.master_lufs)
            if out.get("wav"):
                state.store.update_song(song_id, audio_file=Path(out["mp3"]).name)
                merge_params(song_id, raw_file=raw.name, wav_file=Path(out["wav"]).name,
                             loudness={"before": out["before"], "after": out["after"]})
        except Exception as exc:  # noqa: BLE001 - keep the unmastered take
            merge_params(song_id, master_error=str(exc)[:200])
    if not scored and await state.aux.can("score"):
        merge_params(song_id, polishing="rating")
        # Rate between renders, not during one: sharing the GPU with ACE-Step slows both.
        async with state.gpu_lock:
            await rate(song_id)
    merge_params(song_id, polishing=None)


async def rate(song_id: str) -> None:
    """Judge score plus lyric clarity for one finished song."""
    cur = state.store.get_song(song_id) or {}
    path = settings.audio_dir / (cur.get("audio_file") or "")
    lyrics = None if cur.get("instrumental") else (cur.get("lyrics") or None)
    lang = (cur.get("params") or {}).get("vocal_language") or None
    scores = await state.aux.score([path], [lyrics], lang) if path.is_file() else None
    if scores:
        merge_params(song_id, rating=scores[0])


async def make_art(song_id: str, prompt: str = "", seed: int | None = None) -> bool:
    """Paint album art for one song with the AI service's image model."""
    song = state.store.get_song(song_id)
    if not song or not await state.aux.can("cover"):
        return False
    if not prompt:
        prompt = (song.get("params") or {}).get("art_prompt") or ""
    if not prompt:
        prompt = await state.planner.art_prompt(song.get("title", ""), song.get("style", ""),
                                                song.get("lyrics", ""))
    if seed is None:
        seed = random.randint(1, 2**31 - 1)  # takes painted in the same second still differ
    merge_params(song_id, art_prompt=prompt, art_status="painting", art_seed=seed)
    name = f"{song_id}_{uuid.uuid4().hex[:6]}.jpg"
    out = await state.aux.cover(prompt, settings.cover_dir / name, seed=seed)
    song = state.store.get_song(song_id)
    if not song:
        if out:
            out.unlink(missing_ok=True)
        return False
    if out:
        old = song.get("cover_file")
        state.store.update_song(song_id, cover_file=name)
        if old and old != name:
            (settings.cover_dir / old).unlink(missing_ok=True)
    merge_params(song_id, art_status=None if out else "failed")
    return bool(out)


def copy_art(src_song: dict, dst_id: str) -> None:
    """Edits, covers and extensions inherit the parent's artwork."""
    name = src_song.get("cover_file")
    if not name or not (settings.cover_dir / name).exists():
        return
    new = f"{dst_id}_{uuid.uuid4().hex[:6]}.jpg"
    shutil.copyfile(settings.cover_dir / name, settings.cover_dir / new)
    state.store.update_song(dst_id, cover_file=new)
    merge_params(dst_id, art_prompt=(src_song.get("params") or {}).get("art_prompt"))


async def run_batch(batch_id: str, song_ids: list[str], req: GenRequest, title: str,
                    parent: dict | None = None) -> None:
    store = state.store
    done: set[int] = set()
    planner_note = ""
    polishers: list[asyncio.Task] = []

    async def progress(msg: str) -> None:
        state.progress[batch_id] = msg

    async def finish(i: int, r: GenResult) -> None:
        """Save one finished take right away so it can be played before the others finish."""
        if i >= len(song_ids) or i in done:
            return
        done.add(i)
        sid = song_ids[i]
        song = store.get_song(sid) or {}
        lyrics = r.lyrics or song.get("lyrics", "")
        params = dict(song.get("params") or {})
        params.update({k: v for k, v in r.extra.items() if v is not None})
        params.update(duration=r.duration, key_scale=r.key_scale, bpm=r.bpm, lyrics=lyrics)
        if planner_note:
            params["planner"] = planner_note
        if req.plans and i < len(req.plans) and req.plans[i].art:
            params["art_prompt"] = req.plans[i].art
        params["polishing"] = "queued" if settings.mastering else None
        store.update_song(
            sid, status="complete", audio_file=r.file.name, duration=r.duration,
            style=r.style or song.get("style", ""), lyrics=lyrics,
            title=title or r.extra.get("title") or song.get("title")
            or make_title(lyrics, req.description, r.style),
            bpm=r.bpm, key_scale=r.key_scale, time_signature=r.time_signature,
            seed=r.seed, params=params,
        )
        t = asyncio.get_running_loop().create_task(polish(sid, bool(r.extra.get("rating"))))
        state.tasks.add(t)
        t.add_done_callback(state.tasks.discard)
        polishers.append(t)

    state.progress[batch_id] = "Waiting in queue"
    async with state.gpu_lock:
        engine = await state.engines.current()
        for sid in song_ids:
            store.update_song(sid, status="running", engine=engine.name)
        req.out_dir = settings.audio_dir
        req.name_prefix = batch_id
        planner_note = await plan_takes(engine, req, progress)
        for sid, plan in zip(song_ids, req.plans):  # show titles, style and lyrics immediately
            store.update_song(sid, title=title or plan.title, style=plan.style, lyrics=plan.lyrics)
        if req.max_mode and await state.aux.can("score"):
            req.judge = state.aux.score
        req.on_result = finish
        try:
            results: list[GenResult] = await engine.generate(req, progress)
        except (EngineError, Exception) as exc:  # noqa: BLE001 - report every failure to the UI
            msg = str(exc) or exc.__class__.__name__
            for i, sid in enumerate(song_ids):
                if i not in done:
                    store.update_song(sid, status="failed", error=msg[:500])
            state.progress.pop(batch_id, None)
            return
        for i, r in enumerate(results):
            await finish(i, r)
        for i, sid in enumerate(song_ids):
            if i not in done:
                store.update_song(sid, status="failed", error="engine returned fewer variations")
        finished = [sid for i, sid in enumerate(song_ids) if i in done]
        if parent:
            for sid in finished:
                copy_art(parent, sid)
        elif req.task == "create" and finished and await state.aux.can("cover"):
            state.progress[batch_id] = "Painting the cover art"
            for sid in finished:
                try:
                    await make_art(sid)
                except Exception as exc:  # noqa: BLE001 - art is optional
                    merge_params(sid, art_status="failed", art_error=str(exc)[:200])
    if polishers:
        await asyncio.gather(*polishers, return_exceptions=True)
    state.progress.pop(batch_id, None)


LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
             "pt": "Portuguese", "hi": "Hindi", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
             "ru": "Russian", "ar": "Arabic", "bn": "Bengali", "pa": "Punjabi", "ta": "Tamil"}


async def plan_takes(engine, req: GenRequest, progress) -> str:
    """Simple mode on ACE-Step: let a local LLM write one distinct plan per take."""
    if (engine.name != "acestep" or req.task != "create" or req.mode != "simple"
            or settings.planner == "acestep" or req.kind == "sound"):
        return ""
    planner = state.planner
    if not await planner.available():
        return "acestep (Ollama not running)"
    await progress("Writing the songs" if req.variations > 1 else "Writing the song")
    try:
        plans = await planner.plan_many(
            req.description, req.variations, duration=int(req.duration) if req.duration else None,
            bpm=req.bpm, key=req.key_scale, instrumental=req.instrumental,
            language=LANGUAGES.get((req.vocal_language or "en").lower(), req.vocal_language or "English"),
            style_hint=req.style, vocal_gender=req.vocal_gender, weirdness=req.weirdness,
            style_influence=req.style_influence, variety=req.variety, exclude=req.exclude,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to ACE-Step's own planner
        return f"acestep (planner error: {str(exc)[:120]})"
    req.plans = plans
    return f"ollama:{plans[0].extra.get('planner_model', '')}"


def queue_batch(req: GenRequest, *, title: str, mode: str, task: str, parent_id: str | None = None,
                persona_id: str | None = None, extra_params: dict | None = None,
                parent: dict | None = None) -> list[dict]:
    batch_id = uuid.uuid4().hex[:10]
    songs = []
    created = time.time()  # one timestamp per batch so takes list in order (take 1 first)
    for i in range(req.variations):
        songs.append(state.store.create_song(
            created_at=created,
            batch_id=batch_id, title=title, style=req.style, lyrics=req.lyrics,
            description=req.description, mode=mode, instrumental=req.instrumental, task=task,
            parent_id=parent_id, persona_id=persona_id, status="queued",
            params={"variation": i + 1, **(extra_params or {})},
        ))
    spawn(run_batch(batch_id, [s["id"] for s in songs], req, title, parent))
    return [public_song(s) for s in songs]


def edit_request(parent: dict, task: str, **kw: Any) -> GenRequest:
    style = kw.pop("style", None)
    lyrics = kw.pop("lyrics", None)
    return GenRequest(
        task=task, mode="custom", style=style or parent.get("style", ""),
        lyrics=lyrics if lyrics is not None else parent.get("lyrics", ""),
        instrumental=bool(parent.get("instrumental")), src_audio=require_audio(parent),
        src_duration=parent.get("duration"),
        parent_params={**(parent.get("params") or {}), "bpm": parent.get("bpm"),
                       "key_scale": parent.get("key_scale") or "", "duration": parent.get("duration")},
        **kw,
    )


# ------------------------------------------------------------------ routes
@app.get("/api/status")
async def api_status() -> dict:
    st = await state.engines.status()
    st["version"] = __version__
    st["ffmpeg"] = A.has_ffmpeg()
    aux = await state.aux.health()
    st["aux"] = {"online": bool(aux), "score": bool(aux.get("score")), "cover": bool(aux.get("cover")),
                 "stems": bool(aux.get("stems")), "free_vram_gb": aux.get("free_vram_gb")}
    st["stems"] = S.demucs_available() or bool(aux.get("stems"))
    st["vision"] = await state.planner.vision_model() is not None
    st["mastering"] = settings.mastering and A.has_ffmpeg()
    st["queue"] = sum(1 for s in state.store.list_songs(limit=500) if s["status"] in ("queued", "running"))
    return st


@app.post("/api/generate")
async def api_generate(body: GenerateBody) -> dict:
    if body.mode == "simple" and not body.description.strip():
        raise HTTPException(422, "describe the song you want")
    if body.mode == "custom" and not (body.style.strip() or body.lyrics.strip()):
        raise HTTPException(422, "add lyrics or a style of music")
    reference = None
    style = body.style
    if body.persona_id:
        persona = state.store.get_persona(body.persona_id)
        if not persona:
            raise HTTPException(404, "persona not found")
        if persona["style"] and persona["style"] not in style:
            style = ", ".join(x for x in (persona["style"], style.strip()) if x)
        if persona.get("reference_file"):
            ref = settings.audio_dir / persona["reference_file"]
            reference = ref if ref.exists() else None
    instrumental = body.instrumental
    duration = body.duration
    description = body.description
    if body.kind == "sound":  # Suno "Sounds": short instrumental clips, loops and effects
        instrumental = True
        duration = duration or 12
        if body.mode == "simple":
            description = f"{description.strip()} (a short sound clip, no vocals)"
    req = GenRequest(
        task="create", mode=body.mode, description=description, style=style,
        lyrics=body.lyrics, instrumental=instrumental, variations=body.variations,
        duration=duration, bpm=body.bpm, key_scale=body.key_scale,
        time_signature=body.time_signature, seed=body.seed, vocal_language=body.vocal_language,
        reference_audio=reference, quality=body.quality, kind=body.kind,
        vocal_gender=body.vocal_gender, weirdness=body.weirdness,
        style_influence=body.style_influence, variety=body.variety,
        exclude=body.exclude_styles, max_mode=body.max_mode,
    )
    options = {"kind": body.kind, "max_mode": body.max_mode, "quality": body.quality,
               "weirdness": body.weirdness, "style_influence": body.style_influence,
               "variety": body.variety, "vocal_language": body.vocal_language}
    if body.vocal_gender:
        options["vocal_gender"] = body.vocal_gender
    if body.exclude_styles.strip():
        options["exclude"] = body.exclude_styles.strip()
    songs = queue_batch(req, title=body.title.strip(), mode=body.mode, task="create",
                        persona_id=body.persona_id, extra_params=options)
    return {"songs": songs}


@app.post("/api/songs/{song_id}/extend")
async def api_extend(song_id: str, body: ExtendBody) -> dict:
    parent = get_song_or_404(song_id)
    start = float(parent.get("duration") or A.duration_of(require_audio(parent)) or 0)
    if start <= 0:
        raise HTTPException(409, "cannot read the clip length")
    req = edit_request(parent, "extend", style=body.style, lyrics=body.lyrics,
                       repaint_start=start, repaint_end=start + body.seconds,
                       variations=body.variations)
    title = f"{parent['title']} (extended)"
    return {"songs": queue_batch(req, title=title, mode="custom", task="extend", parent_id=song_id, parent=parent)}


@app.post("/api/songs/{song_id}/cover")
async def api_cover(song_id: str, body: CoverBody) -> dict:
    parent = get_song_or_404(song_id)
    req = edit_request(parent, "cover", style=body.style, lyrics=body.lyrics,
                       cover_strength=body.strength, variations=body.variations)
    title = body.title.strip() or f"{parent['title']} (cover)"
    return {"songs": queue_batch(req, title=title, mode="custom", task="cover", parent_id=song_id, parent=parent)}


@app.post("/api/songs/{song_id}/replace")
async def api_replace(song_id: str, body: ReplaceBody) -> dict:
    parent = get_song_or_404(song_id)
    dur = float(parent.get("duration") or 0)
    if body.end <= body.start:
        raise HTTPException(422, "end must be after start")
    if dur and body.end > dur + 0.5:
        raise HTTPException(422, f"section must end before {dur:.1f}s")
    if body.end - body.start < 3:
        raise HTTPException(422, "replace at least 3 seconds")
    if body.end - body.start > 90:
        raise HTTPException(422, "replace at most 90 seconds at a time")
    req = edit_request(parent, "replace", style=body.style, lyrics=body.lyrics,
                       repaint_start=body.start, repaint_end=body.end, variations=body.variations)
    title = f"{parent['title']} (edit {body.start:.0f}-{body.end:.0f}s)"
    return {"songs": queue_batch(req, title=title, mode="custom", task="replace", parent_id=song_id,
                                 extra_params={"section": [body.start, body.end]}, parent=parent)}


@app.post("/api/songs/{song_id}/remaster")
async def api_remaster(song_id: str) -> dict:
    """Suno-style Remaster: re-render the song closely (cover at high strength, best quality)."""
    parent = get_song_or_404(song_id)
    req = edit_request(parent, "cover", cover_strength=0.9, variations=1, quality="high")
    title = f"{parent['title']} (remaster)"
    return {"songs": queue_batch(req, title=title, mode="custom", task="remaster", parent_id=song_id,
                                 parent=parent)}


@app.post("/api/songs/{song_id}/edit")
async def api_edit(song_id: str, body: EditBody) -> dict:
    """Instant edits (crop, remove a section, reverse, speed, fades) as a new song."""
    parent = get_song_or_404(song_id)
    src = require_audio(parent)
    dur = float(parent.get("duration") or A.duration_of(src) or 0)
    if body.op in ("crop", "remove"):
        if body.end <= body.start:
            raise HTTPException(422, "end must be after start")
        if dur and body.end > dur + 0.5:
            raise HTTPException(422, f"section must end before {dur:.1f}s")
        if body.op == "crop" and body.end - body.start < 1:
            raise HTTPException(422, "keep at least 1 second")
        if body.op == "remove" and dur and body.end - body.start > dur - 1:
            raise HTTPException(422, "that would remove the whole song")
    if not A.has_ffmpeg():
        raise HTTPException(501, "editing needs ffmpeg")
    name = f"edit_{uuid.uuid4().hex[:10]}.mp3"
    out = settings.audio_dir / name
    try:
        await asyncio.to_thread(A.edit, src, out, body.op, start=body.start, end=body.end,
                                factor=body.factor, seconds=body.seconds, duration=dur or None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"edit failed: {str(exc)[:200]}") from exc
    labels = {"crop": f"crop {body.start:.0f}-{body.end:.0f}s", "remove": f"cut {body.start:.0f}-{body.end:.0f}s",
              "reverse": "reversed", "speed": f"{body.factor:g}x", "fade_in": "fade in", "fade_out": "fade out"}
    song = state.store.create_song(
        title=f"{parent['title']} ({labels[body.op]})", style=parent.get("style", ""),
        lyrics=parent.get("lyrics", ""), description=parent.get("description", ""),
        instrumental=parent.get("instrumental", False), task="edit", parent_id=song_id,
        status="complete", audio_file=name, duration=A.duration_of(out), engine="ffmpeg",
        bpm=parent.get("bpm"), key_scale=parent.get("key_scale"), time_signature=parent.get("time_signature"),
        params={"edit": body.model_dump()},
    )
    copy_art(parent, song["id"])
    return {"song": public_song(state.store.get_song(song["id"]))}


@app.post("/api/songs/{song_id}/stems")
async def api_stems(song_id: str, body: Optional[StemsBody] = None) -> dict:
    parent = get_song_or_404(song_id)
    src = require_audio(parent)
    n = (body.stems if body else 4)
    use_aux = await state.aux.can("stems")
    if not use_aux:
        if not S.demucs_available():
            raise HTTPException(501, "Stems need Demucs: start the AI service or run pip install demucs")
        n = 2
    names = ("vocals", "drums", "bass", "other") if n == 4 else ("vocals", "instrumental")
    label = {"other": "melody & other"}
    batch_id = uuid.uuid4().hex[:10]
    created = [state.store.create_song(batch_id=batch_id, title=f"{parent['title']} ({label.get(name, name)})",
                                       style=parent["style"], lyrics=parent["lyrics"] if name == "vocals" else "",
                                       instrumental=name != "vocals", task="stem",
                                       parent_id=song_id, status="queued", params={"stem": name})
               for name in names]

    async def work() -> None:
        state.progress[batch_id] = "Separating stems"
        async with state.gpu_lock:
            for s in created:
                state.store.update_song(s["id"], status="running", engine="demucs")
            try:
                if use_aux:
                    paths = await state.aux.stems(src, settings.audio_dir, n)
                else:
                    paths = await asyncio.to_thread(S.separate, src, settings.audio_dir)
            except Exception as exc:  # noqa: BLE001
                for s in created:
                    state.store.update_song(s["id"], status="failed", error=str(exc)[:500])
                return
            finally:
                state.progress.pop(batch_id, None)
        for s in created:
            p = paths.get(s["params"]["stem"])
            if not p:
                state.store.update_song(s["id"], status="failed", error="stem missing from Demucs output")
                continue
            state.store.update_song(s["id"], status="complete", audio_file=Path(p).name,
                                    duration=A.duration_of(Path(p)) or parent.get("duration"))
            copy_art(parent, s["id"])

    spawn(work())
    return {"songs": [public_song(s) for s in created]}


# ------------------------------------------------------------- cover art
@app.get("/api/songs/{song_id}/art")
async def api_art(song_id: str):
    song = get_song_or_404(song_id)
    name = song.get("cover_file")
    path = settings.cover_dir / name if name else None
    if not path or not path.exists():
        raise HTTPException(404, "no cover art yet")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=31536000, immutable"})


@app.post("/api/songs/{song_id}/art")
async def api_art_generate(song_id: str, body: CoverArtBody) -> dict:
    """Paint (or repaint) the cover. An empty prompt reuses or writes one from the song."""
    get_song_or_404(song_id)
    if not await state.aux.can("cover"):
        raise HTTPException(501, "Cover art needs the AI service: run setup_ai_service.bat once, then start_all.bat")
    merge_params(song_id, art_status="queued")

    async def work() -> None:
        async with state.gpu_lock:
            try:
                await make_art(song_id, body.prompt.strip())
            except Exception as exc:  # noqa: BLE001
                merge_params(song_id, art_status="failed", art_error=str(exc)[:200])

    spawn(work())
    return {"song": public_song(state.store.get_song(song_id))}


@app.post("/api/songs/{song_id}/art/upload")
async def api_art_upload(song_id: str, file: UploadFile = File(...)) -> dict:
    song = get_song_or_404(song_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(415, "upload a JPG, PNG or WebP image")
    data = await file.read(15 * (1 << 20) + 1)
    if len(data) > 15 * (1 << 20):
        raise HTTPException(413, "image is larger than 15 MB")
    name = f"{song_id}_{uuid.uuid4().hex[:6]}.jpg"
    try:
        from PIL import Image  # optional: normalise to a square JPEG
        import io

        img = Image.open(io.BytesIO(data)).convert("RGB")
        side = min(img.size)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((1024, 1024))
        img.save(settings.cover_dir / name, quality=92)
    except ImportError:
        name = name[:-4] + (".png" if ext == ".png" else ".jpg")
        (settings.cover_dir / name).write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"could not read that image: {str(exc)[:120]}") from exc
    old = song.get("cover_file")
    state.store.update_song(song_id, cover_file=name)
    if old and old != name:
        (settings.cover_dir / old).unlink(missing_ok=True)
    merge_params(song_id, art_status=None, art_prompt="(uploaded)")
    return {"song": public_song(state.store.get_song(song_id))}


@app.post("/api/inspire")
async def api_inspire(file: UploadFile = File(...), note: str = Form("")) -> dict:
    """Image inspiration: a local vision model turns a picture into a song idea."""
    import base64

    data = await file.read(15 * (1 << 20) + 1)
    if len(data) > 15 * (1 << 20):
        raise HTTPException(413, "image is larger than 15 MB")
    try:
        return await state.planner.inspire(base64.b64encode(data).decode(), note)
    except RuntimeError as exc:
        raise HTTPException(501, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"vision model failed: {str(exc)[:200]}") from exc


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), title: str = Form("")) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD:
        raise HTTPException(415, f"upload one of {', '.join(sorted(ALLOWED_UPLOAD))}")
    name = f"upload_{uuid.uuid4().hex[:10]}{ext}"
    dest = settings.audio_dir / name
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > 100 * (1 << 20):
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "upload is larger than 100 MB")
            fh.write(chunk)
    duration = A.duration_of(dest)
    clean_title = title.strip() or re.sub(r"[_-]+", " ", Path(file.filename or "Upload").stem)[:80]
    song = state.store.create_song(title=clean_title, task="upload", status="complete",
                                   audio_file=name, duration=duration, engine="upload",
                                   params={"duration": duration})
    return {"song": public_song(song)}


@app.get("/api/songs")
async def api_songs(liked: bool = False, q: str = "") -> dict:
    return {"songs": [public_song(s) for s in state.store.list_songs(liked=liked, q=q)]}


@app.get("/api/songs/{song_id}")
async def api_song(song_id: str) -> dict:
    return {"song": public_song(get_song_or_404(song_id))}


@app.patch("/api/songs/{song_id}")
async def api_patch(song_id: str, body: SongPatch) -> dict:
    get_song_or_404(song_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"song": public_song(state.store.update_song(song_id, **fields))}  # type: ignore[arg-type]


@app.delete("/api/songs/{song_id}")
async def api_delete(song_id: str) -> dict:
    song = get_song_or_404(song_id)
    if song["status"] in ("queued", "running"):
        raise HTTPException(409, "wait until the song finishes")
    state.store.delete_song(song_id)
    params = song.get("params") or {}
    for name in (song.get("audio_file"), params.get("raw_file"), params.get("wav_file")):
        if name:
            (settings.audio_dir / name).unlink(missing_ok=True)
    for vid in settings.audio_dir.glob(f"{song_id}_*.mp4"):
        vid.unlink(missing_ok=True)
    if song.get("cover_file"):
        (settings.cover_dir / song["cover_file"]).unlink(missing_ok=True)
    return {"deleted": song_id}


@app.get("/api/songs/{song_id}/audio")
async def api_audio(song_id: str, download: bool = False, format: str = "mp3", v: str = ""):
    song = get_song_or_404(song_id)
    path = require_audio(song)
    params = song.get("params") or {}
    if v and v == params.get("raw_file") and (settings.audio_dir / v).exists():
        path = settings.audio_dir / v  # the unmastered take, for players that started on it
    if format == "wav":
        wav = settings.audio_dir / params["wav_file"] if params.get("wav_file") else None
        if wav and wav.exists():
            path = wav
        elif path.suffix.lower() != ".wav":
            if not A.has_ffmpeg():
                raise HTTPException(501, "WAV export needs ffmpeg")
            wav = settings.audio_dir / f"{path.stem}.wav"
            if not wav.exists():
                await asyncio.to_thread(A._ff, ["-i", str(path), "-c:a", "pcm_s16le", str(wav)])
            path = wav
    elif format == "mp4":
        if not A.has_ffmpeg():
            raise HTTPException(501, "video export needs ffmpeg")
        cover = settings.cover_dir / song["cover_file"] if song.get("cover_file") else None
        vid = settings.audio_dir / f"{song_id}_{(song.get('cover_file') or 'plain').split('.')[0]}.mp4"
        if not vid.exists():
            async with state.video_lock:
                if not vid.exists():
                    await asyncio.to_thread(A.to_video, path, vid, cover)
        path = vid
    elif format != "mp3":
        raise HTTPException(422, "format must be mp3, wav or mp4")
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
             ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
             ".opus": "audio/ogg", ".mp4": "video/mp4"}.get(path.suffix.lower(), "application/octet-stream")
    filename = None
    if download:
        safe = re.sub(r"[^\w\s()-]", "", song["title"] or "song").strip() or "song"
        filename = f"{safe}{path.suffix}"
    return FileResponse(path, media_type=media, filename=filename)


@app.post("/api/lyrics")
async def api_lyrics(body: LyricsBody) -> dict:
    engine = await state.engines.current()
    return await write_lyrics(engine, body.prompt, body.style)


@app.get("/api/idea")
async def api_idea() -> dict:
    engine = await state.engines.current()
    idea = await engine.random_idea()
    if not idea:
        idea = await state.engines.demo.random_idea()
    return idea


@app.get("/api/personas")
async def api_personas() -> dict:
    return {"personas": state.store.list_personas()}


@app.post("/api/personas")
async def api_persona_create(body: PersonaBody) -> dict:
    song = get_song_or_404(body.song_id)
    src = require_audio(song)
    ref_name = f"persona_{uuid.uuid4().hex[:10]}{src.suffix}"
    shutil.copyfile(src, settings.audio_dir / ref_name)
    name = body.name.strip() or f"{song['title']} persona"
    return {"persona": state.store.create_persona(name, song.get("style", ""), song["id"], ref_name)}


@app.delete("/api/personas/{persona_id}")
async def api_persona_delete(persona_id: str) -> dict:
    persona = state.store.get_persona(persona_id)
    if not persona:
        raise HTTPException(404, "persona not found")
    state.store.delete_persona(persona_id)
    if persona.get("reference_file"):
        (settings.audio_dir / persona["reference_file"]).unlink(missing_ok=True)
    return {"deleted": persona_id}


@app.exception_handler(EngineError)
async def engine_error(_, exc: EngineError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.middleware("http")
async def no_stale_web_files(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"  # always revalidate the app's own files
    return response


app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")


def run() -> None:
    import uvicorn

    uvicorn.run("server.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
