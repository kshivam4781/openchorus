#!/usr/bin/env bash
# Clone ACE-Step 1.5 next to OpenChorus and start its REST API on http://127.0.0.1:8001
set -e
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -d ACE-Step-1.5 ] || git clone https://github.com/ace-step/ACE-Step-1.5
cd ACE-Step-1.5
uv sync
echo "Starting ACE-Step API. The first run downloads the models (several GB)."
exec uv run acestep-api
