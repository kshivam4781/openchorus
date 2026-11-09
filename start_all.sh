#!/usr/bin/env bash
# Start ACE-Step 1.5 (if installed next to this folder), the AI service (if installed)
# and OpenChorus in the background. Logs go to ./logs. Stop everything with ./stop_all.sh
set -e
cd "$(dirname "$0")"
ROOT="$PWD"
mkdir -p logs
ACE="$ROOT/../ACE-Step-1.5"

launch() {  # launch <name> <dir> <command...>  in its own process group, so stop_all.sh stops children too
  local name=$1 dir=$2; shift 2
  pushd "$dir" >/dev/null
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$@" > "$ROOT/logs/$name.log" 2>&1 < /dev/null &
  else
    nohup "$@" > "$ROOT/logs/$name.log" 2>&1 < /dev/null &
  fi
  echo $! > "$ROOT/logs/$name.pid"
  popd >/dev/null
}

export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

if [ -f "$ACE/pyproject.toml" ]; then
  echo "Starting ACE-Step 1.5 on http://127.0.0.1:8001 ..."
  launch acestep "$ACE" env ACESTEP_NO_INIT=false uv run --no-sync acestep-api --host 127.0.0.1 --port 8001
else
  echo "ACE-Step not found at $ACE. Run ./setup_acestep.sh for AI songs with vocals."
fi

if [ -x ai_service/.venv/bin/python ]; then
  echo "Starting the AI service on http://127.0.0.1:8002 (quality judge, cover art, stems) ..."
  launch ai_service "$ROOT/ai_service" .venv/bin/python server.py
else
  echo "AI service not installed. Run ./setup_ai_service.sh for cover art, Max Mode and 4-stem splits."
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi
echo "Starting OpenChorus on http://127.0.0.1:7777 ..."
launch openchorus "$ROOT" .venv/bin/python -m server.main
sleep 3
if command -v open >/dev/null 2>&1 && [ "$(uname)" = Darwin ]; then
  open http://127.0.0.1:7777 || true
elif command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  (xdg-open http://127.0.0.1:7777 >/dev/null 2>&1 &)
fi
echo "Running. Logs are in ./logs. Stop with ./stop_all.sh"
