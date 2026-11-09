"""Client for the optional aux AI service (quality judge, cover art, stems)."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from .config import settings


class AuxClient:
    def __init__(self, url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.url = (url or settings.aux_url).rstrip("/")
        self._transport = transport
        self._health: tuple[float, dict] | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=timeout, transport=self._transport)

    async def health(self, refresh: bool = False) -> dict:
        now = time.monotonic()
        if not refresh and self._health and now - self._health[0] < 15:
            return self._health[1]
        try:
            async with self._client(3.0) as c:
                r = await c.get("/health")
                r.raise_for_status()
                data = r.json()
        except Exception:
            data = {}
        self._health = (now, data)
        return data

    async def can(self, feature: str) -> bool:
        return bool((await self.health()).get(feature))

    async def score(self, paths: list[Path], lyrics: list[str | None] | None = None,
                    language: str | None = None) -> list[dict] | None:
        body: dict = {"paths": [str(p) for p in paths]}
        if lyrics and any(lyrics):
            body["lyrics"] = list(lyrics)
            body["language"] = language or None
        try:
            async with self._client(900.0) as c:
                r = await c.post("/score", json=body)
                r.raise_for_status()
                return r.json()["scores"]
        except Exception:
            return None

    async def cover(self, prompt: str, out_path: Path, seed: int | None = None) -> Path | None:
        try:
            async with self._client(600.0) as c:
                r = await c.post("/cover", json={"prompt": prompt, "out_path": str(out_path), "seed": seed})
                r.raise_for_status()
        except Exception:
            return None
        return out_path if out_path.exists() else None

    async def stems(self, path: Path, out_dir: Path, n: int = 4) -> dict[str, Path]:
        async with self._client(3600.0) as c:
            r = await c.post("/stems", json={"path": str(path), "out_dir": str(out_dir), "stems": n})
            if r.status_code >= 400:
                raise RuntimeError(r.json().get("detail", r.text)[:300])
            return {k: Path(v) for k, v in r.json()["stems"].items()}
