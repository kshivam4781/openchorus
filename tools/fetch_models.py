"""Download Hugging Face repos with curl, file by file, resumable.

huggingface_hub's snapshot_download can stall on some Windows setups (seen with the
Xet backend and in hidden background windows). curl is simple and resumes with -C -.

    python fetch_models.py <repo_id> <dest_dir> [glob ...]
"""
from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi


PART = 128 * 2**20  # bytes per ranged request for big files
PARALLEL = 8


def _curl(curl: str, url: str, out: Path, rng: str | None = None) -> int:
    # Abort a connection that crawls below 20 kB/s for 60 s; the caller retries on a fresh one.
    cmd = [curl, "-sS", "-L", "--fail", "--retry", "8", "--retry-delay", "3",
           "--speed-limit", "20000", "--speed-time", "60", "-o", str(out)]
    cmd += ["-r", rng] if rng else ["-C", "-"]
    return subprocess.run(cmd + [url]).returncode


def _ranged(curl: str, url: str, out: Path, size: int) -> bool:
    """Big files: several ranged connections at once (single CDN streams can crawl),
    each part resumable, then joined."""
    from concurrent.futures import ThreadPoolExecutor

    parts = []
    for i, start in enumerate(range(0, size, PART)):
        end = min(size, start + PART) - 1
        parts.append((out.with_name(f"{out.name}.part{i:03d}"), start, end))

    def get(part):
        path, start, end = part
        want = end - start + 1
        for _ in range(10):
            if path.exists() and path.stat().st_size == want:
                return True
            path.unlink(missing_ok=True)  # parts are small: redo a broken one whole
            _curl(curl, url, path, f"{start}-{end}")
        return path.exists() and path.stat().st_size == want

    with ThreadPoolExecutor(PARALLEL) as ex:
        ok = all(ex.map(get, parts))
    if not ok:
        return False
    tmp = out.with_name(out.name + ".joining")
    with tmp.open("wb") as fh:
        for path, _, _ in parts:
            with path.open("rb") as src:
                shutil.copyfileobj(src, fh, 8 * 2**20)
    tmp.replace(out)
    for path, _, _ in parts:
        path.unlink(missing_ok=True)
    return out.stat().st_size == size


def fetch(repo: str, dest: Path, patterns: list[str]) -> None:
    info = HfApi().model_info(repo, files_metadata=True)
    files = [(s.rfilename, s.size or 0) for s in info.siblings
             if not patterns or any(fnmatch.fnmatch(s.rfilename, p) for p in patterns)]
    total = sum(sz for _, sz in files)
    print(f"{repo}: {len(files)} files, {total / 2**30:.2f} GB -> {dest}", flush=True)
    curl = shutil.which("curl.exe") or shutil.which("curl") or "curl"
    for name, size in files:
        out = dest / name
        if out.exists() and size and out.stat().st_size == size:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{repo}/resolve/{info.sha}/{name}"
        t = time.time()
        if size > 2 * PART:
            out.unlink(missing_ok=True)  # a partial single-stream file is replaced by parts
            if not _ranged(curl, url, out, size):
                raise SystemExit(f"failed to download {name}")
        else:
            for _ in range(6):
                if _curl(curl, url, out) == 0 and (not size or out.stat().st_size == size):
                    break
                time.sleep(3)
            else:
                raise SystemExit(f"failed to download {name}")
        mb = out.stat().st_size / 2**20
        print(f"  {name}  {mb:.0f} MB  {time.time() - t:.0f}s", flush=True)
    print(f"{repo}: done", flush=True)


if __name__ == "__main__":
    fetch(sys.argv[1], Path(sys.argv[2]), sys.argv[3:])
