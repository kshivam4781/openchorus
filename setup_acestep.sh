#!/usr/bin/env bash
# Clone ACE-Step 1.5 next to OpenChorus and install it. Start everything with ./start_all.sh
set -e
cd "$(dirname "$0")/.."
if ! command -v git >/dev/null 2>&1; then
  echo "git is required: install it first (macOS: xcode-select --install, Ubuntu: sudo apt install git)"; exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -d ACE-Step-1.5 ] || git clone https://github.com/ace-step/ACE-Step-1.5
cd ACE-Step-1.5
uv sync
echo
echo "ACE-Step is installed. Start everything with ./start_all.sh in the openchorus folder."
echo "The first start downloads the models (about 10 GB)."
