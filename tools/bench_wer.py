"""Lyric intelligibility: transcribe benchmark renders with faster-whisper and compute the
word error rate against the intended lyrics (lower is clearer singing).

    python tools/bench_wer.py bench/<label> [bench/<label2> ...]
Needs faster-whisper (ACE-Step's environment has it).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def words(text: str) -> list[str]:
    text = re.sub(r"\[[^\]]*\]", " ", text.lower())
    return re.findall(r"[a-z']+", text)


def wer(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def rescore(d: Path, res: list[dict]) -> None:
    """Fill in judge scores from the AI service for runs made before it was up."""
    import httpx

    todo = [r for r in res if "error" not in r and not (r.get("quality") or {}).get("overall")]
    if not todo:
        return
    paths = [str((d / f"{r['name']}.mp3").resolve()) for r in todo]
    try:
        resp = httpx.post("http://127.0.0.1:8002/score", json={"paths": paths}, timeout=900)
        resp.raise_for_status()
        scores = resp.json()["scores"]
    except Exception as exc:  # noqa: BLE001 - the judge may not be downloaded yet
        print("judge unavailable:", str(exc)[:120], flush=True)
        return
    for r, sc in zip(todo, scores):
        r["quality"] = sc
        print(f"{d.name:14s} {r['name']:5s} judge overall={sc['overall']}", flush=True)


def main() -> None:
    from faster_whisper import WhisperModel

    for d in map(Path, sys.argv[1:]):
        res = json.loads((d / "results.json").read_text())
        rescore(d, res)
        (d / "results.json").write_text(json.dumps(res, indent=2))

    try:
        import torch  # noqa: F401  (puts the CUDA libraries that torch ships on the DLL path)
        model = WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cuda", compute_type="float16")
    except Exception as exc:  # noqa: BLE001
        print("whisper on CPU:", exc, flush=True)
        model = WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cpu", compute_type="int8")
    for d in map(Path, sys.argv[1:]):
        res = json.loads((d / "results.json").read_text())
        for r in res:
            if not r.get("lyrics") or "error" in r or "wer" in r:
                continue
            segs, _ = model.transcribe(str(d / f"{r['name']}.mp3"), language="en", vad_filter=True)
            hyp = " ".join(s.text for s in segs)
            r["wer"] = round(wer(words(r["lyrics"]), words(hyp)), 3)
            r["heard"] = hyp[:400]
            print(f"{d.name:14s} {r['name']:5s} WER={r['wer']:.2f}", flush=True)
        (d / "results.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
