#!/usr/bin/env bash
# Start OpenChorus on http://127.0.0.1:7777
set -e
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Note: ffmpeg not found. Install it for MP3 output and uploads (brew install ffmpeg / sudo apt install ffmpeg)."
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
( sleep 2; (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:7777) || (command -v open >/dev/null && open http://127.0.0.1:7777) || true ) >/dev/null 2>&1 &
exec python -m server.main
