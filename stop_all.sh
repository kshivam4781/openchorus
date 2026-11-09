#!/usr/bin/env bash
# Stop what start_all.sh started.
cd "$(dirname "$0")" || exit 1
for name in openchorus ai_service acestep; do
  pidfile="logs/$name.pid"
  [ -f "$pidfile" ] || continue
  pid=$(cat "$pidfile")
  if kill -0 "$pid" 2>/dev/null; then
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true   # the whole process group, else the process
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    echo "Stopped $name"
  fi
  rm -f "$pidfile"
done
