#!/usr/bin/env bash
# Start OpenChorus on http://127.0.0.1:7777 (stop with Ctrl+C).
set -e
cd "$(dirname "$0")"
. scripts/find_python.sh
check_ffmpeg
make_venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
PORT="${OPENCHORUS_PORT:-7777}"
if [ -n "${OPENCHORUS_NO_BROWSER:-}" ]; then
  :
elif [ "$(uname)" = Darwin ]; then
  (sleep 3; open "http://127.0.0.1:$PORT") >/dev/null 2>&1 &
elif command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  (sleep 3; xdg-open "http://127.0.0.1:$PORT") >/dev/null 2>&1 &
fi
exec .venv/bin/python -m server.main
