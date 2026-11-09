"""Runtime configuration, read from environment variables (or a .env file)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # Where songs, the database and uploads live.
    data_dir: Path = Path(os.environ.get("OPENCHORUS_DATA", ROOT / "data")).resolve()

    # "auto" uses ACE-Step when its server answers /health, else the demo synth.
    # "acestep" forces ACE-Step, "demo" forces the offline synth.
    engine: str = os.environ.get("OPENCHORUS_ENGINE", "auto").lower()

    # ACE-Step 1.5 REST API (start it with `uv run acestep-api`).
    acestep_url: str = os.environ.get("ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/")
    acestep_api_key: str = os.environ.get("ACESTEP_API_KEY", "")
    acestep_model: str = os.environ.get("ACESTEP_MODEL", "")  # empty = server default
    acestep_thinking: bool = _bool("ACESTEP_THINKING", True)
    acestep_steps: int = int(os.environ.get("ACESTEP_STEPS", "8"))
    acestep_timeout: float = float(os.environ.get("ACESTEP_TIMEOUT", "900"))
    acestep_poll_seconds: float = float(os.environ.get("ACESTEP_POLL_SECONDS", "1.5"))

    # Song planner for Simple mode and "Write lyrics": a local LLM through Ollama.
    # auto = use Ollama when it is running, else ACE-Step's own planner; acestep = never Ollama.
    planner: str = os.environ.get("OPENCHORUS_PLANNER", "auto").lower()
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "llama3.1")

    # Optional aux AI service (aux/server.py): quality judge, cover art, stems.
    aux_url: str = os.environ.get("AUX_URL", "http://127.0.0.1:8002").rstrip("/")
    # Post-process every song to streaming loudness (-14 LUFS, -1 dBTP).
    master_lufs: float = float(os.environ.get("OPENCHORUS_MASTER_LUFS", "-14"))
    mastering: bool = _bool("OPENCHORUS_MASTERING", True)
    # Ollama vision model for "Image" inspiration.
    vision_model: str = os.environ.get("OLLAMA_VISION_MODEL", "llava:7b")

    host: str = os.environ.get("OPENCHORUS_HOST", "127.0.0.1")
    port: int = int(os.environ.get("OPENCHORUS_PORT", "7777"))

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def cover_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "openchorus.db"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.audio_dir, self.upload_dir, self.cover_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
