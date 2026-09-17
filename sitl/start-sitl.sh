#!/usr/bin/env bash
# Launch ArduCopter SITL and stream MAVLink to the mavlink-bridge service.
# Home position defaults to Chennai (13.0827 N, 80.2707 E) — override via env.

set -euo pipefail

LAT="${SITL_HOME_LAT:-13.0827}"
LON="${SITL_HOME_LON:-80.2707}"
ALT="${SITL_HOME_ALT:-10}"
HDG="${SITL_HOME_HDG:-0}"
SPEEDUP="${SITL_SPEEDUP:-1}"   # 1 = real-time; bump to 5 for faster demo

cd /home/ardu/ardupilot

# Skip MAVProxy — the mavlink-bridge connects directly to the ArduCopter
# binary's native TCP listener on port 5760 (serial0). --out flags below
# would be MAVProxy args and are ignored without it; do NOT re-add them.
# The container binds :5760 so the bridge (docker network) can reach it.
exec Tools/autotest/sim_vehicle.py \
    --vehicle=ArduCopter \
    --frame=quad \
    --no-mavproxy \
    --speedup="${SPEEDUP}" \
    --custom-location="${LAT},${LON},${ALT},${HDG}" \
    --add-param-file=/home/ardu/defaults.parm
