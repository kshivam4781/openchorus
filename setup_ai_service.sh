#!/usr/bin/env bash
# Install the OpenChorus AI service (quality judge, lyric clarity, cover art, 4-stem split)
# on Linux or macOS. About 8 GB of downloads the first time.
set -e
cd "$(dirname "$0")/ai_service"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -d .venv ] || uv venv --python 3.11 .venv
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA GPU found: installing PyTorch with CUDA"
  uv pip install --python .venv/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cu128
elif [ "$(uname)" = Darwin ]; then
  echo "macOS: installing PyTorch with Apple Silicon (MPS) support"
  uv pip install --python .venv/bin/python torch torchaudio
else
  echo "No NVIDIA GPU: installing the smaller CPU build of PyTorch"
  uv pip install --python .venv/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cpu
fi
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download as s
s("segmind/SSD-1B", allow_patterns=["*.json", "*.txt", "*fp16.safetensors", "tokenizer*/*", "tokenizer_2/*"])
s("latent-consistency/lcm-lora-ssd-1b")
s("facebook/audiobox-aesthetics")
s("mobiuslabsgmbh/faster-whisper-large-v3-turbo")
print("Models ready")
PY
echo "Done. ./start_all.sh now starts the AI service on port 8002."
