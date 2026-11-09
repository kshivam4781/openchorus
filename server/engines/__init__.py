"""Engine selection: ACE-Step when its models are ready, else the offline demo synth."""
from __future__ import annotations

import asyncio
import time

from ..config import settings
from .acestep import AceStepEngine
from .base import Engine, EngineError, GenRequest, GenResult
from .demo import DemoEngine

__all__ = ["EngineManager", "Engine", "EngineError", "GenRequest", "GenResult"]


class EngineManager:
    def __init__(self) -> None:
        self.acestep = AceStepEngine()
        self.demo = DemoEngine()
        self._cached: tuple[float, Engine] | None = None
        self._warm_task: asyncio.Task | None = None
        self._last_warm: float | None = None

    def _maybe_warm(self) -> None:
        """ACE-Step is up but its models are not loaded: load them in the background."""
        ace = self.acestep
        if not ace.online or ace.ready or ace.warming:
            return
        if self._warm_task and not self._warm_task.done():
            return
        # Don't hammer a failing init. (None, not 0: the monotonic clock starts near zero at
        # boot, so `now - 0 < 300` would skip the warm-up for five minutes after a restart.)
        if self._last_warm is not None and time.monotonic() - self._last_warm < 300:
            return
        self._last_warm = time.monotonic()
        self._warm_task = asyncio.get_running_loop().create_task(ace.warm_up())

    async def current(self, refresh: bool = False) -> Engine:
        mode = settings.engine
        if mode == "demo":
            return self.demo
        if mode == "acestep":
            return self.acestep
        now = time.monotonic()
        if not refresh and self._cached and now - self._cached[0] < 15:
            return self._cached[1]
        engine: Engine = self.acestep if await self.acestep.available() else self.demo
        self._maybe_warm()
        self._cached = (now, engine)
        return engine

    async def status(self) -> dict:
        engine = await self.current(refresh=True)
        ace = self.acestep
        return {
            "active": engine.info(),
            "mode": settings.engine,
            "acestep": {"url": ace.base_url, "online": ace.online, "ready": ace.ready,
                        "warming": ace.warming, "warm_error": ace.warm_error,
                        "models": ace.info().get("models", [])},
        }
