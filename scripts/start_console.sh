#!/usr/bin/env bash
# Start the operator console on the Jetson (127.0.0.1:8000; reach it through a tunnel).
set -euo pipefail
. "$(dirname "$0")/_common.sh"
if is_running console; then echo "console already running (pid $(cat "$RUN/console.pid"))"; exit 0; fi
cd "$ROOT"
nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${VLA_PORT:-8000}" --timeout-graceful-shutdown 3 \
  >"$ROOT/data/logs/console.out" 2>&1 &
echo $! >"$RUN/console.pid"
echo "console starting (pid $!), log: data/logs/console.out"
echo "open http://localhost:${VLA_PORT:-8000} on your laptop after forwarding port ${VLA_PORT:-8000}"
