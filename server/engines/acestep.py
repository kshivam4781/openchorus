"""ACE-Step 1.5 engine: talks to the official ACE-Step REST API server.

Start the model server with:
    git clone https://github.com/ace-step/ACE-Step-1.5 && cd ACE-Step-1.5
    uv sync && uv run acestep-api          # listens on http://127.0.0.1:8001

API reference: https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .. import audio as A
from ..config import settings
from .base import Engine, EngineError, GenRequest, GenResult, Progress

INSTRUMENTAL = "[Instrumental]"
MAX_REPAINT_SECONDS = 90.0  # ACE-Step docs: repaint works on 3 to 90 second ranges


def _form_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _sung(lyrics: str) -> str | None:
    """Lyrics worth checking for clarity (None for instrumentals)."""
    if not lyrics or "[instrumental]" in lyrics.lower():
        return None
    return lyrics


async def pick_best(cands: list[GenResult], keep: int, judge) -> list[GenResult]:
    """Max Mode: score every candidate with the quality judge and keep the best."""
    if len(cands) <= keep:
        return cands
    scores = await judge([c.file for c in cands], [_sung(c.lyrics) for c in cands]) if judge else None
    if scores and len(scores) == len(cands):
        for c, sc in zip(cands, scores):
            c.extra["rating"] = sc
        ranked = sorted(cands, key=lambda c: c.extra["rating"].get("rank", c.extra["rating"].get("overall", 0)),
                        reverse=True)
    else:
        ranked = cands
    for c in ranked[keep:]:
        c.file.unlink(missing_ok=True)
    best = ranked[:keep]
    for c in best:
        c.extra["candidates"] = len(cands)
    return best


class AceStepEngine(Engine):
    name = "acestep"
    label = "ACE-Step 1.5"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = (base_url or settings.acestep_url).rstrip("/")
        self.api_key = settings.acestep_api_key if api_key is None else api_key
        self._transport = transport
        self._models: list[str] = []
        self.online = False
        self.ready = False
        self.warming = False
        self.warm_error = ""

    # ---- http ------------------------------------------------------------
    def _client(self, timeout: float = 60.0) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout,
                                 transport=self._transport)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        if resp.status_code == 429:
            raise EngineError("ACE-Step queue is full, try again shortly")
        if resp.status_code == 401:
            raise EngineError("ACE-Step rejected the API key (set ACESTEP_API_KEY)")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail") or resp.text
            except Exception:
                detail = resp.text
            raise EngineError(f"ACE-Step error {resp.status_code}: {str(detail)[:300]}")
        body = resp.json()
        if isinstance(body, dict) and "data" in body:
            if body.get("error"):
                raise EngineError(f"ACE-Step error: {body['error']}")
            return body["data"]
        return body

    async def check(self) -> None:
        """Refresh `online` (server answers) and `ready` (models loaded) from /health."""
        try:
            async with self._client(timeout=3.0) as c:
                data = self._unwrap(await c.get("/health"))
        except Exception:
            self.online = self.ready = False
            return
        self.online = isinstance(data, dict) and data.get("status") in ("ok", "healthy", True)
        # ACE-Step loads its models lazily; until then a request would block on the
        # multi-GB download. Older servers without the flag count as ready.
        self.ready = self.online and data.get("models_initialized", True) is not False
        if self.ready:
            self.warming = False
            if not self._models:
                await self._load_models()

    async def available(self) -> bool:
        await self.check()
        return self.ready

    async def warm_up(self) -> None:
        """Ask ACE-Step to download/load its models (POST /v1/init). Runs once at a time."""
        if self.warming:
            return
        self.warming = True
        try:
            async with self._client(timeout=3600.0) as c:
                self._unwrap(await c.post("/v1/init", json={"init_llm": True}))
        except Exception as exc:  # noqa: BLE001 - surfaced through status, retried later
            self.warm_error = str(exc)[:300]
        finally:
            self.warming = False

    async def _load_models(self) -> None:
        """DiT model names. /v1/model_inventory has the documented shape; current servers
        answer /v1/models with an OpenAI-style list ({"data": [{"id": "acestep/<name>"}]})."""
        names: list[str] = []
        for path in ("/v1/model_inventory", "/v1/models"):
            try:
                async with self._client(timeout=10.0) as c:
                    data = self._unwrap(await c.get(path))
            except Exception:
                continue
            if isinstance(data, dict):  # documented shape: {"models": [{"name": ...}]}
                names = [str(m["name"]) for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
            elif isinstance(data, list):  # OpenAI style: [{"id": "acestep/<name>"}]
                names = [str(m["id"]).split("/")[-1] for m in data if isinstance(m, dict) and m.get("id")]
            if names:
                break
        self._models = names

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "url": self.base_url, "models": self._models}

    # ---- payload building -----------------------------------------------
    def build_payload(self, req: GenRequest) -> dict[str, Any]:
        """Translate an OpenChorus request into ACE-Step /release_task fields."""
        p: dict[str, Any] = {
            # WAV is written much faster by ACE-Step than MP3; OpenChorus encodes with ffmpeg.
            "audio_format": "wav" if A.has_ffmpeg() else "mp3",
            "batch_size": max(1, min(8, req.variations)),
            "inference_steps": settings.acestep_steps,
            "vocal_language": req.vocal_language or "en",
        }
        if settings.acestep_model:
            p["model"] = settings.acestep_model
        # Suno-style "Weirdness" and "Style Influence" steer ACE-Step's language model.
        p["lm_temperature"] = round(0.6 + 0.5 * max(0, min(100, req.weirdness)) / 100, 3)
        p["lm_cfg_scale"] = round(1.5 + 2.5 * max(0, min(100, req.style_influence)) / 100, 3)
        if req.seed is not None:
            p["use_random_seed"] = False
            p["seed"] = int(req.seed)
        for key, val in (("bpm", req.bpm), ("key_scale", req.key_scale),
                         ("time_signature", req.time_signature)):
            if val:
                p[key] = val

        if req.task == "create":
            p["task_type"] = "text2music"
            if req.duration:
                p["audio_duration"] = float(req.duration)
            if req.mode == "simple" and not req.instrumental:
                # The 5Hz LM writes caption, lyrics and metadata from a description.
                # ACE-Step's sample mode overwrites duration/bpm/key with the LM's own
                # choices (llm_generation_inputs.py), so pass user settings as hints.
                hints = []
                if req.duration:
                    hints.append(f"about {int(req.duration)} seconds long")
                if req.bpm:
                    hints.append(f"{req.bpm} BPM")
                if req.key_scale:
                    hints.append(f"in {req.key_scale}")
                for k in ("audio_duration", "bpm", "key_scale", "time_signature"):
                    p.pop(k, None)
                p["sample_query"] = req.description + (f" ({', '.join(hints)})" if hints else "")
                p["thinking"] = True
                if req.style:
                    p["prompt"] = req.style
            else:
                p["prompt"] = req.style or req.description
                if (req.vocal_gender in ("male", "female") and not req.instrumental
                        and "vocal" not in p["prompt"].lower()):
                    p["prompt"] = f"{p['prompt']}, {req.vocal_gender} vocals".strip(", ")
                p["lyrics"] = INSTRUMENTAL if req.instrumental else (req.lyrics or "")
                # "high": the 5Hz LM sketches the song as audio codes first (best structure,
                # about 25 s per minute of audio on a 12 GB laptop GPU). "fast": DiT only.
                p["thinking"] = settings.acestep_thinking and req.quality != "fast"
                if req.planned:
                    # Our planner already wrote the caption, language and metadata, so skip
                    # ACE-Step's chain-of-thought rewrite of them (about 7 s per take).
                    p["use_cot_caption"] = False
                    p["use_cot_language"] = False
                    p["use_cot_metas"] = False
                if req.mode == "simple":
                    p["use_format"] = True
        elif req.task == "cover":
            p.update(task_type="cover", prompt=req.style,
                     lyrics=INSTRUMENTAL if req.instrumental else req.lyrics,
                     audio_cover_strength=float(req.cover_strength))
        elif req.task == "replace":
            p.update(task_type="repaint", prompt=req.style,
                     lyrics=INSTRUMENTAL if req.instrumental else req.lyrics,
                     repainting_start=float(req.repaint_start),
                     repainting_end=float(req.repaint_end), chunk_mask_mode="explicit")
        elif req.task == "extend":
            # ACE-Step's documented way to "continue writing": pad the clip with
            # silence and repaint the new tail using the existing audio as context.
            p.update(task_type="repaint", prompt=req.style,
                     lyrics=INSTRUMENTAL if req.instrumental else req.lyrics,
                     repainting_start=float(req.repaint_start),
                     repainting_end=float(req.repaint_end),
                     audio_duration=float(req.repaint_end), chunk_mask_mode="explicit")
        else:
            raise EngineError(f"unsupported task {req.task}")
        return p

    # ---- generation -----------------------------------------------------
    async def generate(self, req: GenRequest, progress: Progress) -> list[GenResult]:
        if not req.plans:
            return await self._generate_task(req, progress)
        # One ACE-Step task per planned take: each has its own lyrics, style, tempo, key.
        out: list[GenResult] = []
        n = len(req.plans)
        for i, plan in enumerate(req.plans):
            sub = dataclasses.replace(
                req, mode="custom", plans=[], variations=2 if req.max_mode else 1,
                style=plan.style, lyrics=plan.lyrics,
                bpm=plan.bpm, key_scale=plan.key, duration=float(plan.duration),
                time_signature=req.time_signature or "4",
                name_prefix=f"{req.name_prefix}_t{i}", on_result=None, planned=True,
                seed=(req.seed + i) if req.seed is not None else None,
            )

            async def take_progress(msg: str, i: int = i) -> None:
                await progress(f"Take {i + 1} of {n}: {msg}" if n > 1 else msg)

            cands = await self._generate_task(sub, take_progress)
            if len(cands) > 1:
                await take_progress("Judging candidates")
            res = (await pick_best(cands, 1, req.judge))[0]
            res.extra["title"] = plan.title
            res.extra.update(plan.extra)
            res.bpm, res.key_scale = plan.bpm, plan.key
            out.append(res)
            if req.on_result:
                await req.on_result(i, res)
        return out

    async def _generate_task(self, req: GenRequest, progress: Progress) -> list[GenResult]:
        req.out_dir.mkdir(parents=True, exist_ok=True)
        keep = req.variations
        if req.max_mode and not req.planned and req.task == "create":
            req = dataclasses.replace(req, variations=min(8, req.variations * 2))
        payload = self.build_payload(req)
        files: dict[str, tuple[str, bytes, str]] = {}
        src = req.src_audio
        tmp_padded: Path | None = None
        if req.task == "extend":
            if not src:
                raise EngineError("extend needs a source clip")
            extra = float(req.repaint_end) - float(req.repaint_start)
            if extra > MAX_REPAINT_SECONDS:
                raise EngineError(f"extend by at most {int(MAX_REPAINT_SECONDS)} seconds at a time")
            tmp_padded = req.out_dir / f"{req.name_prefix}_padded.wav"
            await asyncio.to_thread(A.pad_to, src, tmp_padded, float(req.repaint_end))
            src = tmp_padded
        if src:
            files["src_audio"] = (src.name, src.read_bytes(), "application/octet-stream")
        if req.reference_audio and req.task == "create":
            files["reference_audio"] = (req.reference_audio.name, req.reference_audio.read_bytes(),
                                        "application/octet-stream")

        await progress("Sending to ACE-Step")
        try:
            async with self._client(timeout=120.0) as c:
                if files:
                    data = {k: _form_value(v) for k, v in payload.items() if v is not None}
                    resp = await c.post("/release_task", data=data, files=files)
                else:
                    resp = await c.post("/release_task", json=payload)
                task = self._unwrap(resp)
                task_id = task["task_id"]
                results = await self._poll(c, task_id, progress)
                await progress("Downloading audio")
                out: list[GenResult] = []
                for i, item in enumerate(results):
                    out.append(await self._download(c, item, req, i))
        except httpx.HTTPError as exc:
            raise EngineError(f"could not reach ACE-Step at {self.base_url}: "
                              f"{exc.__class__.__name__} {exc}".strip()) from exc
        finally:
            if tmp_padded:
                tmp_padded.unlink(missing_ok=True)
        if not out:
            raise EngineError("ACE-Step finished but returned no audio")
        if len(out) > keep:
            await progress("Judging candidates")
            out = await pick_best(out, keep, req.judge)
        return out

    async def _poll(self, c: httpx.AsyncClient, task_id: str, progress: Progress) -> list[dict]:
        start = time.monotonic()
        last_msg = ""
        first_error_at: float | None = None
        while True:
            if time.monotonic() - start > settings.acestep_timeout:
                raise EngineError("ACE-Step took too long (raise ACESTEP_TIMEOUT)")
            try:
                rows = self._unwrap(await c.post("/query_result", json={"task_id_list": [task_id]}))
                first_error_at = None
            except httpx.TransportError as exc:
                # The ACE-Step server can stall briefly while the GPU is busy; keep polling
                # for up to two minutes before giving up on the task.
                first_error_at = first_error_at or time.monotonic()
                if time.monotonic() - first_error_at > 120:
                    raise EngineError(f"lost contact with ACE-Step ({exc.__class__.__name__})") from exc
                await progress("Waiting for ACE-Step to respond")
                await asyncio.sleep(3)
                continue
            row = rows[0] if isinstance(rows, list) and rows else {}
            status = row.get("status", 0)
            if status == 1:
                return self._parse_result(row.get("result"))
            items = self._parse_items(row.get("result"))
            first = items[0] if items else {}
            if status == 2:
                detail = first.get("error") or row.get("progress_text") or "generation failed"
                raise EngineError(f"ACE-Step failed: {str(detail)[:300]}")
            elapsed = int(time.monotonic() - start)
            pct = first.get("progress")
            stage = first.get("stage") or ""
            if isinstance(pct, (int, float)) and pct > 0:
                msg = f"Generating {int(pct * 100 if pct <= 1 else pct)}%"
            elif stage and stage != "queued":
                msg = f"Generating ({stage}, {elapsed}s)"
            elif stage == "queued":
                msg = f"Queued on ACE-Step ({elapsed}s)"
            else:
                msg = f"Generating with ACE-Step ({elapsed}s)"
            if msg != last_msg:
                await progress(msg)
                last_msg = msg
            await asyncio.sleep(settings.acestep_poll_seconds)

    @staticmethod
    def _parse_items(raw: Any) -> list[dict]:
        """`result` is a JSON string holding a list of items (see query_result_service.py)."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                return []
        if isinstance(raw, dict):
            raw = [raw]
        return [r for r in (raw or []) if isinstance(r, dict)]

    @classmethod
    def _parse_result(cls, raw: Any) -> list[dict]:
        return [r for r in cls._parse_items(raw) if r.get("file")]

    async def _download(self, c: httpx.AsyncClient, item: dict, req: GenRequest, idx: int) -> GenResult:
        url = item["file"]
        if not url.startswith("http"):
            url = urljoin(self.base_url + "/", url.lstrip("/"))
        resp = await c.get(url, timeout=300.0)
        if resp.status_code >= 400:
            raise EngineError(f"could not download audio ({resp.status_code})")
        ext = ".mp3"
        ctype = resp.headers.get("content-type", "")
        if "wav" in ctype:
            ext = ".wav"
        elif "flac" in ctype:
            ext = ".flac"
        path = req.out_dir / f"{req.name_prefix}_{idx}{ext}"
        path.write_bytes(resp.content)
        if ext == ".wav" or resp.content[:4] == b"RIFF":
            if ext != ".wav":
                path = path.rename(path.with_suffix(".wav"))
            path = await asyncio.to_thread(A.to_mp3, path, path.with_suffix(".mp3"))

        metas = item.get("metas") or {}
        seeds = str(item.get("seed_value") or "").split(",")
        seed = None
        if idx < len(seeds) and seeds[idx].strip().lstrip("-").isdigit():
            seed = int(seeds[idx])
        duration = metas.get("duration")
        if not duration:
            duration = await asyncio.to_thread(A.duration_of, path)
        bpm = metas.get("bpm")
        return GenResult(
            file=path,
            duration=float(duration) if duration else None,
            style=item.get("prompt") or req.style,
            lyrics=item.get("lyrics") or req.lyrics,
            bpm=int(bpm) if isinstance(bpm, (int, float)) or str(bpm).isdigit() else None,
            key_scale=str(metas.get("keyscale") or metas.get("key_scale") or req.key_scale or ""),
            time_signature=str(metas.get("timesignature") or metas.get("time_signature") or ""),
            seed=seed,
            extra={"dit_model": item.get("dit_model"), "lm_model": item.get("lm_model"),
                   "genres": metas.get("genres")},
        )

    # ---- helpers ----------------------------------------------------------
    async def write_lyrics(self, prompt: str, style: str = "") -> dict | None:
        """Use ACE-Step's LM (/format_input) to draft lyrics from a description."""
        try:
            async with self._client(timeout=120.0) as c:
                data = self._unwrap(await c.post("/format_input", json={
                    "prompt": style or prompt,
                    "lyrics": f"[Verse 1]\n{prompt}" if prompt else "",
                    "temperature": 0.85,
                    "param_obj": json.dumps({"language": "en"}),
                }))
        except Exception:
            return None
        lyrics = (data or {}).get("lyrics") or ""
        if len(lyrics.strip().splitlines()) < 4:
            return None
        return {"lyrics": lyrics, "style": (data or {}).get("caption") or style}

    async def random_idea(self) -> dict | None:
        try:
            async with self._client(timeout=30.0) as c:
                data = self._unwrap(await c.post("/create_random_sample",
                                                 json={"sample_type": random.choice(["simple_mode", "custom_mode"])}))
        except Exception:
            return None
        caption = (data or {}).get("caption")
        if not caption:
            return None
        return {"description": caption, "style": caption, "lyrics": data.get("lyrics", "")}
