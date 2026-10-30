#!/usr/bin/env bash
# Start the Pi motion service in SIMULATION on this machine (no GPIO is touched).
set -euo pipefail
. "$(dirname "$0")/_common.sh"
if is_running sim_pi; then echo "simulated Pi already running (pid $(cat "$RUN/sim_pi.pid"))"; exit 0; fi
cd "$ROOT/pi_service"
PI_BACKEND=simulation PI_HOST="${PI_HOST:-127.0.0.1}" PI_PORT="${PI_PORT:-8100}" \
PI_API_TOKEN="${VLA_PI_API_TOKEN:-}" \
  nohup "$ROOT/.venv/bin/python" -m excavator_pi >"$ROOT/data/logs/sim_pi.out" 2>&1 &
echo $! >"$RUN/sim_pi.pid"
echo "simulated Pi starting on ${PI_HOST:-127.0.0.1}:${PI_PORT:-8100} (pid $!), log: data/logs/sim_pi.out"
echo "it starts LATCHED: use 'Rearm Pi controller' in the dashboard before a task can run"
