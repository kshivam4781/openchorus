"""Audio helpers: WAV I/O with numpy, plus ffmpeg for everything else."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def write_wav(path: Path, audio: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """Write float audio in [-1, 1], shape (n,) or (n, channels), as 16-bit PCM."""
    if audio.ndim == 1:
        audio = audio[:, None]
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit WAV is supported without ffmpeg")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return data.reshape(-1, ch), sr


def duration_of(path: Path) -> float | None:
    """Duration in seconds, or None if it cannot be determined."""
    if has_ffmpeg():
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=30, check=True,
            ).stdout
            return round(float(json.loads(out)["format"]["duration"]), 2)
        except Exception:
            pass
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return round(w.getnframes() / float(w.getframerate()), 2)
        except Exception:
            return None
    return None


def to_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "192k") -> Path:
    """Encode to MP3 when ffmpeg exists; otherwise keep the WAV."""
    if not has_ffmpeg():
        return wav_path
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-codec:a",
         "libmp3lame", "-b:a", bitrate, str(mp3_path)],
        check=True, timeout=300,
    )
    wav_path.unlink(missing_ok=True)
    return mp3_path


def pad_to(src: Path, dst: Path, total_seconds: float) -> Path:
    """Write `src` padded with silence up to `total_seconds` as a WAV file."""
    if has_ffmpeg():
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af",
             f"apad=whole_dur={total_seconds:.3f}", "-ar", str(SAMPLE_RATE),
             "-ac", "2", str(dst)],
            check=True, timeout=300,
        )
        return dst
    audio, sr = read_wav(src)
    target = int(total_seconds * sr)
    if target > len(audio):
        audio = np.vstack([audio, np.zeros((target - len(audio), audio.shape[1]), np.float32)])
    write_wav(dst, audio, sr)
    return dst


def decode_to_array(path: Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio file to a float32 stereo array at `sr`."""
    if path.suffix.lower() == ".wav":
        audio, file_sr = read_wav(path)
        if file_sr == sr:
            return audio if audio.shape[1] == 2 else np.repeat(audio, 2, axis=1)
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg is required to decode this audio file")
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "f32le",
         "-ac", "2", "-ar", str(sr), "-"],
        capture_output=True, check=True, timeout=300,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2).copy()


# --------------------------------------------------------------- mastering
def _ff(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", *args],
                          capture_output=True, text=True, timeout=timeout, check=True)


def measure_loudness(path: Path) -> dict | None:
    """EBU R128 loudness (I, TP, LRA, thresh) via ffmpeg loudnorm's analysis pass."""
    if not has_ffmpeg():
        return None
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
         "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    text = res.stderr
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def master(src: Path, out_stem: Path, target_lufs: float = -14.0) -> dict:
    """Streaming-style master: two-pass loudness normalisation to -14 LUFS with a
    -1 dBTP ceiling, a gentle low cut and a soft limiter. Writes <out_stem>.wav (24-bit,
    48 kHz) and <out_stem>.mp3 (320 kbps). Returns paths and before/after loudness."""
    if not has_ffmpeg():
        return {"mp3": src, "wav": None, "before": None, "after": None}
    before = measure_loudness(src)
    chain = "highpass=f=25"
    if before:
        chain += (f",loudnorm=I={target_lufs}:TP=-1:LRA=11:measured_I={before['input_i']}:"
                  f"measured_TP={before['input_tp']}:measured_LRA={before['input_lra']}:"
                  f"measured_thresh={before['input_thresh']}:offset={before['target_offset']}:linear=true")
    else:
        chain += f",loudnorm=I={target_lufs}:TP=-1:LRA=11"
    chain += ",alimiter=limit=0.89:attack=5:release=50:level=false"
    wav = out_stem.with_suffix(".wav")
    mp3 = out_stem.with_suffix(".mp3")
    tmp_wav = out_stem.with_name(out_stem.name + "_m.wav")
    _ff(["-i", str(src), "-af", chain, "-ar", "48000", "-c:a", "pcm_s24le", str(tmp_wav)])
    _ff(["-i", str(tmp_wav), "-codec:a", "libmp3lame", "-b:a", "320k", str(mp3) + ".tmp.mp3"])
    os.replace(tmp_wav, wav)
    os.replace(str(mp3) + ".tmp.mp3", mp3)
    after = measure_loudness(mp3)
    return {"mp3": mp3, "wav": wav,
            "before": float(before["input_i"]) if before else None,
            "after": float(after["input_i"]) if after else None}


# ------------------------------------------------------------- edit tools
EDIT_OPS = ("crop", "remove", "reverse", "speed", "fade_in", "fade_out")


def edit(src: Path, out: Path, op: str, *, start: float = 0.0, end: float = 0.0,
         factor: float = 1.0, seconds: float = 3.0, duration: float | None = None) -> Path:
    """Suno-style Edit menu operations, rendered with ffmpeg to an MP3."""
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg is required for editing")
    dur = duration or duration_of(src) or 0
    if op == "crop":
        if not 0 <= start < end:
            raise ValueError("crop needs 0 <= start < end")
        _ff(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-af",
             "afade=t=in:d=0.02,areverse,afade=t=in:d=0.05,areverse", "-b:a", "320k", str(out)])
    elif op == "remove":
        if not 0 <= start < end:
            raise ValueError("remove needs 0 <= start < end")
        xf = min(0.25, max(0.02, (end - start) / 4))
        _ff(["-i", str(src), "-filter_complex",
             f"[0:a]atrim=0:{start:.3f},asetpts=PTS-STARTPTS[a];"
             f"[0:a]atrim={end:.3f},asetpts=PTS-STARTPTS[b];"
             f"[a][b]acrossfade=d={xf:.3f}:c1=tri:c2=tri[o]", "-map", "[o]", "-b:a", "320k", str(out)])
    elif op == "reverse":
        _ff(["-i", str(src), "-af", "areverse", "-b:a", "320k", str(out)])
    elif op == "speed":
        if not 0.5 <= factor <= 2.0:
            raise ValueError("speed must be between 0.5x and 2x")
        _ff(["-i", str(src), "-af", f"atempo={factor:.4f}", "-b:a", "320k", str(out)])
    elif op == "fade_in":
        _ff(["-i", str(src), "-af", f"afade=t=in:st=0:d={seconds:.2f}", "-b:a", "320k", str(out)])
    elif op == "fade_out":
        st = max(0.0, dur - seconds)
        _ff(["-i", str(src), "-af", f"afade=t=out:st={st:.2f}:d={seconds:.2f}", "-b:a", "320k", str(out)])
    else:
        raise ValueError(f"unknown edit {op}")
    return out


def to_video(audio_path: Path, out: Path, image: Path | None = None, color: str = "1f2a3a") -> Path:
    """MP4 with the cover art (or a plain colour) and a moving waveform strip."""
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg is required for video export")
    if image and image.exists():
        inputs = ["-loop", "1", "-i", str(image)]
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c=0x{color}:s=1080x1080:r=25"]
    graph = ("[0:v]scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080,setsar=1[bg];"
             "[1:a]showwaves=s=1080x160:mode=cline:colors=0xf2b33d@0.9:rate=25[w];"
             "[bg][w]overlay=0:H-200:shortest=1,format=yuv420p[v]")
    _ff([*inputs, "-i", str(audio_path), "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", "25", "-c:a", "aac", "-b:a", "192k",
         "-shortest", "-movflags", "+faststart", str(out)], timeout=1800)
    return out
