"""OpenChorus AI service (folder ai_service; "aux" is a reserved name on Windows): quality judge, cover art and stem separation.

Runs next to ACE-Step in its own environment (it needs PyTorch + CUDA):
    python -m venv .venv  (or uv venv)
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
    pip install -r requirements.txt
    python server.py                     # http://127.0.0.1:8002

Models (downloaded on first use):
  * Quality judge: facebook/audiobox-aesthetics (CC-BY-4.0). Scores 1-10 for
    Content Enjoyment (CE), Content Usefulness (CU), Production Complexity (PC)
    and Production Quality (PQ).
  * Cover art: segmind/SSD-1B (Apache-2.0) + latent-consistency/lcm-lora-ssd-1b
    (OpenRAIL++), 4-step 1024x1024.
  * Stems: Demucs htdemucs (MIT), 2 or 4 stems.

Every model is loaded lazily and the GPU cache is emptied after each job so the
music model keeps as much VRAM as possible.
"""
from __future__ import annotations

import asyncio
import gc
import os
from concurrent.futures import ThreadPoolExecutor
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

HOST = os.environ.get("AUX_HOST", "127.0.0.1")
PORT = int(os.environ.get("AUX_PORT", "8002"))
COVER_MODEL = os.environ.get("AUX_COVER_MODEL", "segmind/SSD-1B")
COVER_LORA = os.environ.get("AUX_COVER_LORA", "latent-consistency/lcm-lora-ssd-1b")
JUDGE_CKPT = os.environ.get("AUX_JUDGE_CKPT") or None  # local checkpoint.pt, else download

_HERE = Path(__file__).resolve().parent
_MODELS = _HERE / "models"
UNET_FILE = "unet/diffusion_pytorch_model.fp16.safetensors"
LORA_FILE = "pytorch_lora_weights.safetensors"


def _cached(repo: str, filename: str) -> Optional[str]:
    """Path of a file already in the Hugging Face cache, without touching the network."""
    try:
        from huggingface_hub import try_to_load_from_cache

        hit = try_to_load_from_cache(repo, filename)
        return hit if isinstance(hit, str) and os.path.getsize(hit) > 0 else None
    except Exception:
        return None


def _complete(path: Path) -> bool:
    """True when a fetched file exists and no download of it is still running."""
    return path.is_file() and path.stat().st_size > 0 and time.time() - path.stat().st_mtime > 20


def cover_source() -> Optional[tuple[str, str]]:
    """(model, lora) to load, preferring ./models (tools/fetch_models.py), else the HF cache.
    None while the weights are not on disk yet, so nothing ever blocks on a download."""
    local, lora = _MODELS / "SSD-1B", _MODELS / "lcm-lora-ssd-1b"
    weights = [UNET_FILE, "vae/diffusion_pytorch_model.fp16.safetensors",
               "text_encoder/model.fp16.safetensors", "text_encoder_2/model.fp16.safetensors"]
    model = str(local) if all(_complete(local / w) for w in weights) and (local / "model_index.json").exists() else (
        COVER_MODEL if _cached(COVER_MODEL, UNET_FILE) or Path(COVER_MODEL).is_dir() else None)
    adapter = str(lora) if _complete(lora / LORA_FILE) else (
        COVER_LORA if _cached(COVER_LORA, LORA_FILE) or Path(COVER_LORA).is_dir() else None)
    return (model, adapter) if model and adapter else None


def judge_source() -> Optional[str]:
    if JUDGE_CKPT and Path(JUDGE_CKPT).is_file():
        return JUDGE_CKPT
    local = _MODELS / "audiobox-aesthetics" / "checkpoint.pt"
    if _complete(local):
        return str(local)
    return _cached("facebook/audiobox-aesthetics", "checkpoint.pt")


app = FastAPI(title="OpenChorus AI service", version="1.0.0")
_lock = threading.Lock()  # one GPU job at a time
# Every model is created and run on this one thread: CUDA handles made on one worker thread
# and used on another fail with "invalid resource handle" on some Windows drivers.
_gpu = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")


KEEP_LOADED = os.environ.get("AUX_KEEP_LOADED", "").lower() in ("1", "true", "yes")


async def on_gpu(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_gpu, fn, *args)


def _release_scoring() -> None:
    """Drop the judge and Whisper after each job. They reload in seconds, while keeping
    them resident would take ~2.5 GB the music model and the image model need; on Windows
    an over-full GPU silently spills into system memory and everything slows to a crawl."""
    global _judge, _whisper
    if KEEP_LOADED:
        return
    _judge = None
    _whisper = None
    _free_gpu()
_judge = None
_cover = None


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available() and os.environ.get("AUX_DEVICE", "").lower() != "cpu":
        return "mps"  # Apple Silicon
    return "cpu"


def _free_gpu() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:  # noqa: BLE001
        return  # no torch or no CUDA: nothing to free


WHISPER_REPO = os.environ.get("AUX_WHISPER_MODEL", "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
_whisper = None


def whisper_ready() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None and (
        Path(WHISPER_REPO).is_dir() or _cached(WHISPER_REPO, "model.bin") is not None)


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        try:
            import torch  # noqa: F401  (puts torch's CUDA libraries on the DLL path)

            if not _fits(2.5):
                raise RuntimeError("not enough free VRAM")
            _whisper = WhisperModel(WHISPER_REPO, device="cuda", compute_type="float16", local_files_only=True)
        except Exception:
            _whisper = WhisperModel(WHISPER_REPO, device="cpu", compute_type="int8", local_files_only=True,
                                    cpu_threads=max(2, (os.cpu_count() or 4) - 2))
    return _whisper


def _words(text: str) -> list[str]:
    import re

    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text.lower())
    return re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text)


def _wer(ref: list[str], hyp: list[str]) -> float:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(1, len(ref))


def lyric_clarity(path: str, lyrics: str, language: Optional[str]) -> Optional[dict]:
    """How much of the requested lyrics a speech model can hear: 1 - word error rate."""
    ref = _words(lyrics)
    if len(ref) < 8:
        return None
    # Greedy decoding without timestamps: several times faster than the default beam search,
    # which matters when Whisper has to run on the CPU next to a busy music model.
    segs, _ = _get_whisper().transcribe(path, language=language or None, vad_filter=False,
                                         condition_on_previous_text=False, beam_size=1,
                                         without_timestamps=True)
    heard = " ".join(s.text for s in segs)
    hyp = _words(heard)
    # Romanised lyrics (e.g. Hindi or Bengali typed in Latin letters) come back from Whisper
    # in the native script, so word matching would wrongly say nothing was heard. Only
    # score when both sides use the same script.
    if hyp and _latin_share(ref) > 0.5 and _latin_share(hyp) < 0.5:
        return None
    if hyp and _latin_share(ref) < 0.5 and _latin_share(hyp) > 0.5:
        return None
    return {"clarity": round(max(0.0, 1.0 - _wer(ref, hyp)), 3), "heard": heard[:600]}


def _latin_share(words: list[str]) -> float:
    letters = "".join(words)
    return sum(c.isascii() for c in letters) / max(1, len(letters))


def gpu_free_gb() -> Optional[float]:
    """Free VRAM across the whole card, as nvidia-smi reports it. torch's mem_get_info only
    sees this process's budget on Windows (WDDM), so it misses what ACE-Step is holding."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.split()
        return round(int(out[0]) / 1024, 2) if out else None
    except Exception:
        try:
            import torch

            return round(torch.cuda.mem_get_info()[0] / 2**30, 2) if torch.cuda.is_available() else None
        except Exception:
            return None


def _fits(need_gb: float) -> bool:
    """Run on the GPU only when the model fits next to everything else. On Windows an
    over-full GPU does not fail, it spills into system RAM and runs many times slower than
    the CPU would; the music model usually holds most of a 12 GB card."""
    forced = os.environ.get("AUX_DEVICE", "").lower()
    if forced in ("cpu", "cuda"):
        return forced == "cuda"
    try:
        import torch

        if not torch.cuda.is_available():
            return False
    except Exception:
        return False
    free = gpu_free_gb()
    return free is None or free >= need_gb


# ------------------------------------------------------------- quality judge
def _get_judge():
    global _judge
    if _judge is None:
        from audiobox_aesthetics.infer import initialize_predictor

        ckpt = judge_source()
        if not ckpt:
            raise HTTPException(503, "the quality judge model is not downloaded yet")
        if _fits(2.0):
            _judge = initialize_predictor(ckpt)
        else:
            from unittest import mock

            import torch

            # The predictor picks CUDA by itself; load it on the CPU instead.
            with mock.patch.object(torch.cuda, "is_available", return_value=False):
                _judge = initialize_predictor(ckpt)
            # The checkpoint asks for bf16 autocast, which is very slow on most CPUs.
            model = getattr(_judge, "model", None)
            if model is not None and hasattr(model, "enable_autocast"):
                model.enable_autocast = False
                model.precision = torch.float32
            torch.set_num_threads(max(2, (os.cpu_count() or 4) - 2))
    return _judge


class ScoreBody(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=16)
    lyrics: Optional[list[Optional[str]]] = None   # per path; enables the lyric clarity check
    language: Optional[str] = None


@app.post("/score")
async def score_route(body: ScoreBody) -> dict:
    return await on_gpu(score, body)


def score(body: ScoreBody) -> dict:
    for p in body.paths:
        if not Path(p).is_file():
            raise HTTPException(404, f"no such file: {p}")
    with _lock:
        t = time.time()
        judge = _get_judge()
        rows = judge.forward([{"path": p} for p in body.paths])
        _free_gpu()
        out = []
        for r in rows:
            r = {k: round(float(v), 3) for k, v in r.items()}
            # One number on a 1-10 scale: how enjoyable and how well produced.
            r["overall"] = round((r.get("CE", 0) + r.get("PQ", 0)) / 2, 3)
            r["rank"] = r["overall"]
            out.append(r)
        if body.lyrics and whisper_ready():
            for r, path, lyr in zip(out, body.paths, body.lyrics):
                try:
                    c = lyric_clarity(path, lyr or "", body.language) if lyr else None
                except Exception as exc:  # noqa: BLE001 - clarity is a bonus signal
                    c = None
                    r["clarity_error"] = str(exc)[:160]
                if c:
                    r.update(c)
                    # Mumbled or missing lyrics are the most common flaw in AI songs:
                    # up to 2 points for words a listener can actually make out.
                    r["rank"] = round(r["overall"] + 2.0 * c["clarity"], 3)
        _release_scoring()
    return {"scores": out, "seconds": round(time.time() - t, 2)}


# ---------------------------------------------------------------- cover art
NEGATIVE = ("text, letters, words, watermark, logo, signature, caption, typography, "
            "blurry, lowres, jpeg artifacts, deformed, extra fingers, frame, border")


def _get_cover():
    global _cover
    if _cover is None:
        import torch
        from diffusers import AutoPipelineForText2Image, LCMScheduler

        src = cover_source()
        if not src:
            raise HTTPException(503, "the cover art model is not downloaded yet")
        model, lora = src
        device = _device()
        dtype = torch.float16 if device in ("cuda", "mps") else torch.float32
        pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=dtype, variant="fp16")
        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
        pipe.load_lora_weights(lora)
        pipe.fuse_lora()
        if device == "cuda":
            # Moves each sub-model to the GPU only while it runs: about 3 GB peak.
            pipe.enable_model_cpu_offload()
        elif device == "mps":
            pipe.to("mps")  # Apple Silicon shares memory with the CPU, so no offloading is needed
        pipe.vae.enable_tiling()
        _cover = pipe
    return _cover


class CoverBody(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=1200)
    out_path: str
    seed: Optional[int] = None
    size: int = Field(1024, ge=512, le=1024)
    steps: int = Field(6, ge=2, le=12)


@app.post("/cover")
async def cover_route(body: CoverBody) -> dict:
    return await on_gpu(cover, body)


def cover(body: CoverBody) -> dict:
    import torch

    out = Path(body.out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        t = time.time()
        pipe = _get_cover()
        gen = torch.Generator("cpu").manual_seed(body.seed if body.seed is not None else int(time.time()))
        prompt = (f"{body.prompt}, album cover art, square composition, striking, high detail, "
                  "professional illustration, rich color, dramatic lighting")
        kw = dict(prompt=prompt, negative_prompt=NEGATIVE, num_inference_steps=body.steps,
                  guidance_scale=1.5, width=body.size, height=body.size)
        if _device() == "cuda" and not getattr(pipe, "_oc_sequential", False) and not _fits(4.0):
            # Little VRAM left: stream the model layer by layer (slower, but far faster than
            # spilling into system RAM).
            pipe.enable_sequential_cpu_offload()
            pipe._oc_sequential = True
        try:
            img = pipe(generator=gen, **kw).images[0]
        except torch.cuda.OutOfMemoryError:
            # The music model holds most of the VRAM: stream layers one by one instead.
            _free_gpu()
            pipe.enable_sequential_cpu_offload()
            pipe._oc_sequential = True
            gen = torch.Generator("cpu").manual_seed(body.seed if body.seed is not None else int(time.time()))
            img = pipe(generator=gen, **kw).images[0]
        img.convert("RGB").save(out, quality=92)
        _free_gpu()
    return {"path": str(out), "seconds": round(time.time() - t, 2)}


# ------------------------------------------------------------------- stems
class StemsBody(BaseModel):
    path: str
    out_dir: str
    stems: int = Field(4, ge=2, le=4)


@app.post("/stems")
async def stems_route(body: StemsBody) -> dict:
    return await on_gpu(stems, body)


def stems(body: StemsBody) -> dict:
    src = Path(body.path)
    if not src.is_file():
        raise HTTPException(404, "no such file")
    out_dir = Path(body.out_dir)
    work = out_dir / f"_demucs_{src.stem}"
    cmd = [sys.executable, "-m", "demucs", "-n", "htdemucs", "-o", str(work), "--mp3", "--mp3-bitrate", "320"]
    if body.stems == 2:
        cmd += ["--two-stems", "vocals"]
    cmd.append(str(src))
    with _lock:
        t = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        _free_gpu()
    if res.returncode != 0:
        raise HTTPException(500, f"demucs failed: {res.stderr[-400:]}")
    found = {p.stem: p for p in work.rglob("*.mp3")}
    names = {"no_vocals": "instrumental"}
    result = {}
    for stem, p in found.items():
        name = names.get(stem, stem)
        dest = out_dir / f"{src.stem}_{name}.mp3"
        shutil.move(str(p), dest)
        result[name] = str(dest)
    shutil.rmtree(work, ignore_errors=True)
    return {"stems": result, "seconds": round(time.time() - t, 2)}


def _safe_device() -> str:
    try:
        return _device()
    except Exception:  # noqa: BLE001
        return "cpu"  # torch is not installed


@app.get("/health")
def health() -> dict:
    import importlib.util

    try:
        import torch

        cuda = torch.cuda.is_available()
        free = gpu_free_gb() if cuda else None
    except Exception:
        cuda, free = False, None
    return {
        "status": "ok",
        "score": importlib.util.find_spec("audiobox_aesthetics") is not None and judge_source() is not None,
        "cover": importlib.util.find_spec("diffusers") is not None and cover_source() is not None,
        "stems": importlib.util.find_spec("demucs") is not None,
        "clarity": whisper_ready(),
        "cuda": cuda,
        "device": _safe_device(),
        "free_vram_gb": free,
        "loaded": {"judge": _judge is not None, "cover": _cover is not None},
    }


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
