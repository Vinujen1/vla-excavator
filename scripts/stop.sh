#!/usr/bin/env bash
# Stop the console and the simulated Pi started by the scripts in this folder.
set -uo pipefail
. "$(dirname "$0")/_common.sh"
stop_one console
stop_one sim_pi
