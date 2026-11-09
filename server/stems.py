"""Optional stem separation with Demucs (MIT licence): pip install demucs."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def separate(src: Path, out_dir: Path) -> dict[str, Path]:
    """Split into vocals + instrumental. Returns {"vocals": path, "instrumental": path}."""
    if not demucs_available():
        raise RuntimeError("Stems need Demucs: pip install demucs")
    work = out_dir / "_demucs"
    subprocess.run(
        [sys.executable, "-m", "demucs", "--two-stems=vocals", "--mp3", "-o", str(work), str(src)],
        check=True, timeout=3600,
    )
    found = {p.stem: p for p in work.rglob("*.mp3")}
    if "vocals" not in found or "no_vocals" not in found:
        raise RuntimeError("Demucs did not produce the expected stems")
    vocals = out_dir / f"{src.stem}_vocals.mp3"
    inst = out_dir / f"{src.stem}_instrumental.mp3"
    shutil.move(str(found["vocals"]), vocals)
    shutil.move(str(found["no_vocals"]), inst)
    shutil.rmtree(work, ignore_errors=True)
    return {"vocals": vocals, "instrumental": inst}
