"""Engine interface shared by the ACE-Step engine and the offline demo synth."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

Progress = Callable[[str], Awaitable[None]]


@dataclass
class GenRequest:
    task: str = "create"            # create | extend | cover | replace
    mode: str = "custom"            # simple | custom
    description: str = ""           # simple mode prompt
    style: str = ""                 # "style of music" / caption
    lyrics: str = ""
    instrumental: bool = False
    variations: int = 2
    duration: Optional[float] = None
    bpm: Optional[int] = None
    key_scale: str = ""
    time_signature: str = ""
    seed: Optional[int] = None
    vocal_language: str = "en"
    quality: str = "high"           # high = ACE-Step LM plans the audio codes; fast = DiT only
    planned: bool = False           # caption/lyrics/metadata already written by our planner
    vocal_gender: str = ""          # "", "male", "female"
    weirdness: int = 50             # 0-100: planner and LM creativity
    style_influence: int = 50       # 0-100: how literally to follow the style
    variety: str = "normal"         # low | normal | high: how different the takes are
    kind: str = "song"              # "song" or "sound" (short instrumental clips / FX)
    exclude: str = ""               # styles/instruments to keep out (planner)
    max_mode: bool = False          # render extra candidates and keep the best-scored
    judge: Optional[Callable] = None  # async (list[Path]) -> list[dict] | None
    src_audio: Optional[Path] = None        # cover / replace / extend source
    src_duration: Optional[float] = None
    reference_audio: Optional[Path] = None  # persona style reference
    repaint_start: float = 0.0
    repaint_end: float = 0.0
    cover_strength: float = 0.6
    parent_params: dict = field(default_factory=dict)
    out_dir: Path = Path(".")
    name_prefix: str = "clip"
    plans: list = field(default_factory=list)  # SongPlan per take (Simple mode with a planner)
    on_result: Optional[Callable] = None  # async (take_index, GenResult) -> None, called per take


@dataclass
class GenResult:
    file: Path
    duration: Optional[float] = None
    style: str = ""
    lyrics: str = ""
    bpm: Optional[int] = None
    key_scale: str = ""
    time_signature: str = ""
    seed: Optional[int] = None
    extra: dict = field(default_factory=dict)


class EngineError(RuntimeError):
    pass


class Engine(abc.ABC):
    name = "base"
    label = "Base"

    async def available(self) -> bool:
        return True

    @abc.abstractmethod
    async def generate(self, req: GenRequest, progress: Progress) -> list[GenResult]:
        """Render the request and return one result per take."""

    async def write_lyrics(self, prompt: str, style: str = "") -> Optional[dict]:
        """Return {"lyrics": str, "style": str} or None if unsupported."""
        return None

    async def random_idea(self) -> Optional[dict]:
        return None

    def info(self) -> dict:
        return {"name": self.name, "label": self.label}
