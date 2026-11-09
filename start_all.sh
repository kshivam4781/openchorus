#!/usr/bin/env bash
# Start ACE-Step 1.5 (if installed next to this folder), the AI service (if installed)
# and OpenChorus in the background. Logs go to ./logs. Stop everything with ./stop_all.sh
set -e
set -m   # job control: every background job gets its own process group, so stop_all.sh can stop its children
cd "$(dirname "$0")"
ROOT="$PWD"
. scripts/find_python.sh
check_ffmpeg
mkdir -p logs
ACE="$ROOT/../ACE-Step-1.5"
PORT="${OPENCHORUS_PORT:-7777}"

launch() {  # launch <name> <dir> <command...>
  local name=$1 dir=$2; shift 2
  pushd "$dir" >/dev/null
  nohup "$@" > "$ROOT/logs/$name.log" 2>&1 < /dev/null &
  echo $! > "$ROOT/logs/$name.pid"
  popd >/dev/null
}

export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

if [ -f "$ACE/pyproject.toml" ]; then
  echo "Starting ACE-Step 1.5 on http://127.0.0.1:8001 ..."
  ACE_ENV=(ACESTEP_NO_INIT=false)
  # Decode audio in large chunks on cards with 11 GB+ (much faster); smaller cards keep ACE-Step's default.
  if command -v nvidia-smi >/dev/null 2>&1; then
    vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -dc '0-9')
    if [ -n "$vram" ] && [ "$vram" -ge 11000 ]; then
      ACE_ENV+=(ACESTEP_VAE_DECODE_CHUNK_SIZE=512 ACESTEP_MPS_DECODE_OFFLOAD=false)
    fi
  fi
  command -v uv >/dev/null 2>&1 || export PATH="$HOME/.local/bin:$PATH"
  launch acestep "$ACE" env "${ACE_ENV[@]}" uv run --no-sync acestep-api --host 127.0.0.1 --port 8001
else
  echo "ACE-Step not found at $ACE. Run ./setup_acestep.sh for AI songs with vocals."
fi

if [ -x ai_service/.venv/bin/python ]; then
  echo "Starting the AI service on http://127.0.0.1:8002 (quality judge, cover art, stems) ..."
  launch ai_service "$ROOT/ai_service" .venv/bin/python server.py
else
  echo "AI service not installed. Run ./setup_ai_service.sh for cover art, Max Mode and 4-stem splits."
fi

make_venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
echo "Starting OpenChorus on http://127.0.0.1:$PORT ..."
launch openchorus "$ROOT" .venv/bin/python -m server.main
for _ in $(seq 1 30); do   # wait until it answers, so the browser opens on a working page
  if command -v curl >/dev/null 2>&1 && curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then break; fi
  sleep 1
done
if [ -n "${OPENCHORUS_NO_BROWSER:-}" ]; then
  :
elif [ "$(uname)" = Darwin ]; then
  open "http://127.0.0.1:$PORT" || true
elif command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  (xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1 &)
fi
echo "Running at http://127.0.0.1:$PORT. Logs are in ./logs. Stop with ./stop_all.sh"
