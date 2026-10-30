# Shared helpers for the start/stop scripts.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/data/run"
mkdir -p "$RUN" "$ROOT/data/logs"
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi

is_running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

stop_one() {
  if is_running "$1"; then
    kill -INT "$(cat "$RUN/$1.pid")"            # graceful: lifespan shutdown switches outputs off
    for _ in $(seq 1 50); do is_running "$1" || break; sleep 0.1; done
    # Open video streams can stall a graceful shutdown; the camera lock (flock) and the Pi's
    # outputs-off watchdog don't depend on it, so force it after 5 s.
    is_running "$1" && kill -9 "$(cat "$RUN/$1.pid")"
    echo "stopped $1"
  else
    echo "$1 not running"
  fi
  rm -f "$RUN/$1.pid"
}
