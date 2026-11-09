#!/usr/bin/env bash
# Stop what start_all.sh started.
cd "$(dirname "$0")"
for name in openchorus ai_service acestep; do
  pidfile="logs/$name.pid"
  [ -f "$pidfile" ] || continue
  pid=$(cat "$pidfile")
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true   # whole group, else the process
  rm -f "$pidfile"
  echo "Stopped $name"
done
