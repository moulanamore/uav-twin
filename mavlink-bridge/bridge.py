"""
MAVLink -> MQTT bridge.

Subscribes to ArduPilot SITL over UDP, accumulates the latest values from
BATTERY_STATUS, ESC_TELEMETRY_1_TO_4, SYS_STATUS, ATTITUDE, VFR_HUD, HEARTBEAT,
and emits a snapshot JSON on `uav/<uav_id>/telemetry/state` at TICK_HZ,
using the SAME schema the Python simulator produced in Phase 1a — so the twin
worker and dashboard need no changes.

Also runs a background thread that keeps uploading and starting the demo
mission (see mission.py).

License: Apache-2.0
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field

# pymavlink picks its dialect and wire format at IMPORT TIME from these env
# vars — they must be set BEFORE `from pymavlink import ...`.  ArduPilot uses
# MAVLink 2 and the ardupilotmega dialect (superset of common, includes
# ESC_TELEMETRY_*).
os.environ.setdefault("MAVLINK20", "1")
os.environ.setdefault("MAVLINK_DIALECT", "ardupilotmega")

import paho.mqtt.client as mqtt          # noqa: E402
from pymavlink import mavutil            # noqa: E402

import mission as ap_mission             # noqa: E402


MQTT_HOST      = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
UAV_ID         = os.getenv("UAV_ID", "quad-01")
# Connection string for pymavlink. Default: outbound TCP to ArduCopter's
# native listener on port 5760. To go back to UDP listen mode use
# "udpin:0.0.0.0:14550".
MAVLINK_URL    = os.getenv("MAVLINK_URL", "tcp:sitl:5760")
TICK_HZ        = float(os.getenv("TICK_HZ", "10"))
NUM_MOTORS     = int(os.getenv("NUM_MOTORS", "4"))
NUM_CELLS      = int(os.getenv("NUM_CELLS", "4"))
HOME_LAT       = float(os.getenv("SITL_HOME_LAT", "13.0827"))
HOME_LON       = float(os.getenv("SITL_HOME_LON", "80.2707"))
UPLOAD_MISSION = os.getenv("UPLOAD_MISSION", "true").lower() == "true"

TOPIC_STATE = f"uav/{UAV_ID}/telemetry/state"

# --- RPM synthesis --------------------------------------------------------
# SITL's ESC_TELEMETRY_1_TO_4 message reliably carries current/voltage/temp
# but its rpm field stays at 0 because the simulator uses a thrust-only motor
# model (no rotational dynamics). When we detect a stale RPM but the vehicle
# is armed and drawing throttle, synthesise a plausible RPM from
# throttle * pack_voltage * KV * eta so downstream ML features aren't
# systematically zero on cross_min_rpm.  Set MOTOR_KV/MOTOR_ETA env vars to
# match your real airframe if you know them.
MOTOR_KV     = float(os.getenv("MOTOR_KV", "900"))   # RPM/V no-load
MOTOR_ETA    = float(os.getenv("MOTOR_ETA", "0.85")) # loaded-vs-noload ratio


# ---------------------------------------------------------------------------
# Accumulated MAVLink state — updated in the reader loop, sampled at TICK_HZ.
# ---------------------------------------------------------------------------
@dataclass
class MotorTelem:
    rpm: float = 0.0
    current_a: float = 0.0
    voltage_v: float = 0.0
    temp_c: float = 30.0
    accum_ah: float = 0.0
    last_update: float = 0.0


@dataclass
class State:
    pack_v: float = 0.0
    pack_i_a: float = 0.0
    soc: float = 1.0                    # from BATTERY_STATUS.battery_remaining
    battery_temp_c: float = 30.0
    cells: list[float] = field(default_factory=lambda: [0.0] * NUM_CELLS)
    cell_healthy: list[bool] = field(default_factory=lambda: [True] * NUM_CELLS)
    motors: list[MotorTelem] = field(default_factory=lambda: [MotorTelem() for _ in range(NUM_MOTORS)])
    phase: str = "unknown"
    throttle: float = 0.0
    armed: bool = False
    flight_hours: float = 0.0
    mission_loops: int = 0
    last_flight_flag: bool = False
    last_tick: float = 0.0


state = State()
state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def handle_battery_status(msg):
    """Update battery state from a BATTERY_STATUS message.

    ArduPilot's simulated battery in SITL usually only populates voltages[0]
    (whole-pack voltage) unless a smart-battery driver is configured. When
    that happens we synthesise per-cell values as pack_v/NUM_CELLS with a
    tiny jitter so the dashboard's per-cell chart is meaningful and the
    cell-fail alert doesn't fire on every tick.
    """
    with state_lock:
        # current: cA -> A ; -1 means "not measured"
        if msg.current_battery != -1:
            state.pack_i_a = msg.current_battery / 100.0
        if msg.battery_remaining >= 0:
            state.soc = msg.battery_remaining / 100.0
        if msg.temperature != 32767:
            state.battery_temp_c = msg.temperature / 100.0

        cells_mv = list(msg.voltages)
        real_cells = []            # (idx, mV) for cells that came in populated
        for i in range(NUM_CELLS):
            v = cells_mv[i] if i < len(cells_mv) else 65535
            if v != 65535 and v != 0:
                real_cells.append((i, v))

        if len(real_cells) == NUM_CELLS:
            # Full per-cell telemetry from a smart-battery-configured SITL.
            pack_mv = 0
            for i, v in real_cells:
                state.cells[i] = v / 1000.0
                state.cell_healthy[i] = True
                pack_mv += v
            state.pack_v = pack_mv / 1000.0

        elif len(real_cells) >= 1:
            # SITL default: only voltages[0] populated with the WHOLE PACK
            # voltage (not one cell). Synthesise per-cell from that.
            pack_mv = real_cells[0][1] if real_cells[0][1] > 5000 \
                      else real_cells[0][1] * NUM_CELLS
            state.pack_v = pack_mv / 1000.0
            per_cell = pack_mv / NUM_CELLS / 1000.0
            for i in range(NUM_CELLS):
                # tiny deterministic jitter so cells don't look artificially identical
                jitter = ((i - (NUM_CELLS-1)/2) * 0.005)
                state.cells[i] = round(per_cell + jitter, 4)
                state.cell_healthy[i] = True

        # else: no battery info at all this tick — keep last known state


def handle_sys_status(msg):
    with state_lock:
        # voltage_battery: mV -> V ; current_battery: cA -> A
        if msg.voltage_battery > 0 and state.pack_v == 0.0:
            state.pack_v = msg.voltage_battery / 1000.0
        if msg.current_battery != -1 and state.pack_i_a == 0.0:
            state.pack_i_a = msg.current_battery / 100.0


def handle_esc_telemetry(msg, base_idx: int):
    """Handle ESC_TELEMETRY_1_TO_4 (base 0) / _5_TO_8 (base 4) etc."""
    rpm_arr = msg.rpm          # cRPM (0.01 RPM) per ArduPilot conv? Actually just RPM
    volt_arr = msg.voltage     # cV = 0.01 V
    curr_arr = msg.current     # cA = 0.01 A
    temp_arr = msg.temperature # degC
    with state_lock:
        for k in range(4):
            idx = base_idx + k
            if idx >= NUM_MOTORS:
                break
            m = state.motors[idx]
            m.rpm = float(rpm_arr[k])
            m.voltage_v = volt_arr[k] / 100.0
            m.current_a = curr_arr[k] / 100.0
            m.temp_c = float(temp_arr[k])
            m.last_update = time.time()


def handle_vfr_hud(msg):
    with state_lock:
        state.throttle = msg.throttle / 100.0


def handle_heartbeat(msg):
    from pymavlink.dialects.v20 import ardupilotmega as mavlink
    armed = bool(msg.base_mode & mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    # ArduPilot mode strings via custom_mode; we map a few:
    ac_modes = {0:"STABILIZE",1:"ACRO",2:"ALT_HOLD",3:"AUTO",4:"GUIDED",
                5:"LOITER",6:"RTL",7:"CIRCLE",9:"LAND",16:"POSHOLD",
                17:"BRAKE",18:"THROW",19:"AVOID_ADSB",20:"GUIDED_NOGPS",
                21:"SMART_RTL",22:"FLOWHOLD",23:"FOLLOW",24:"ZIGZAG"}
    mode_str = ac_modes.get(int(msg.custom_mode), f"mode_{msg.custom_mode}")
    with state_lock:
        state.armed = armed
        # Very rough phase mapping:
        if not armed:
            state.phase = "landed"
        elif mode_str in ("RTL","LAND"):
            state.phase = "descent"
        elif mode_str == "AUTO":
            state.phase = "cruise"
        elif mode_str == "GUIDED":
            state.phase = "takeoff"
        else:
            state.phase = mode_str.lower()


def read_loop(conn):
    print("[bridge] MAVLink reader loop starting", flush=True)
    while True:
        try:
            msg = conn.recv_match(blocking=True, timeout=2)
        except Exception as e:
            print(f"[bridge] recv error: {e}", flush=True); time.sleep(1); continue
        if msg is None:
            continue
        t = msg.get_type()
        try:
            if t == "BATTERY_STATUS":
                handle_battery_status(msg)
            elif t == "SYS_STATUS":
                handle_sys_status(msg)
            elif t == "ESC_TELEMETRY_1_TO_4":
                handle_esc_telemetry(msg, 0)
            elif t == "ESC_TELEMETRY_5_TO_8":
                handle_esc_telemetry(msg, 4)
            elif t == "VFR_HUD":
                handle_vfr_hud(msg)
            elif t == "HEARTBEAT":
                handle_heartbeat(msg)
            elif t == "STATUSTEXT":
                # print anything at severity <= MAV_SEVERITY_WARNING (0-4)
                sev = getattr(msg, "severity", 6)
                if sev <= 4:
                    text = getattr(msg, "text", b"")
                    if isinstance(text, bytes):
                        text = text.decode(errors="replace").rstrip("\x00")
                    print(f"[ap sev={sev}] {text}", flush=True)
            elif t == "COMMAND_ACK":
                # log arm/mode failures with the actual result code
                if msg.command in (400, 176):  # ARM_DISARM, SET_MODE
                    cmd = "ARM" if msg.command == 400 else "SET_MODE"
                    print(f"[ap] {cmd} ack result={msg.result}", flush=True)
        except Exception as e:
            print(f"[bridge] handler error on {t}: {e}", flush=True)


def snapshot() -> dict:
    """Take a snapshot of accumulated state in the Phase 1a JSON schema."""
    now = time.time()
    with state_lock:
        # accrue flight hours while armed
        if state.last_tick != 0.0 and state.armed:
            state.flight_hours += (now - state.last_tick) / 3600.0
        # increment loop counter on falling-edge of flying flag
        if state.last_flight_flag and not state.armed:
            state.mission_loops += 1
        state.last_flight_flag = state.armed
        state.last_tick = now

        # If SITL didn't populate a real RPM, synthesise it from throttle x
        # pack voltage x KV. Only kicks in when armed and throttle > 1% so
        # ground-idle telemetry stays at rpm=0 (correct).
        def _rpm_of(m: MotorTelem) -> float:
            if m.rpm > 1.0:
                return round(m.rpm, 1)
            if state.armed and state.throttle > 0.01 and state.pack_v > 0.1:
                return round(state.throttle * state.pack_v * MOTOR_KV * MOTOR_ETA, 1)
            return 0.0
        motors_out = [{
            "idx": i, "rpm": _rpm_of(m),
            "current_a": round(m.current_a,3),
            "voltage_v": round(m.voltage_v,3) if m.voltage_v > 0 else round(state.pack_v,3),
            "temp_c": round(m.temp_c,2),
            "accum_ah": round(m.accum_ah,4),
        } for i, m in enumerate(state.motors)]
        cells_out = [{
            "idx": i, "v": round(state.cells[i],4),
            "healthy": state.cell_healthy[i],
        } for i in range(NUM_CELLS)]
        return {
            "ts": now, "uav_id": UAV_ID,
            "phase": state.phase, "throttle": round(state.throttle,3),
            "battery": {
                "pack_v": round(state.pack_v,3),
                "pack_i_a": round(state.pack_i_a,3),
                "soc": round(state.soc,4),
                "temp_c": round(state.battery_temp_c,2),
                "cells": cells_out,
            },
            "motors": motors_out,
            "flight_hours": round(state.flight_hours,4),
            "mission_loops": state.mission_loops,
            "_source": "mavlink",
        }


def publish_loop(client):
    dt = 1.0 / TICK_HZ
    next_tick = time.monotonic()
    while True:
        snap = snapshot()
        client.publish(TOPIC_STATE, json.dumps(snap), qos=0)
        next_tick += dt
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_tick = time.monotonic()


def main():
    # MQTT
    client = mqtt.Client(client_id=f"bridge-{UAV_ID}",
                         callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c,u,f,rc,p: print(f"[bridge] mqtt connected rc={rc}", flush=True)
    print(f"[bridge] connecting to mqtt://{MQTT_HOST}:{MQTT_PORT} …", flush=True)
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60); break
        except Exception as e:
            print(f"[bridge] mqtt not ready ({e}); retrying …", flush=True); time.sleep(2)
    client.loop_start()

    # Connect to ArduCopter's native MAVLink listener. TCP is bidirectional,
    # so mission uploads and mode commands also flow this way.
    print(f"[bridge] connecting MAVLink at {MAVLINK_URL}", flush=True)
    while True:
        try:
            conn = mavutil.mavlink_connection(MAVLINK_URL, source_system=255,
                                              autoreconnect=True, retries=3)
            break
        except Exception as e:
            print(f"[bridge] MAVLink connect failed ({e}); retrying …", flush=True)
            time.sleep(3)

    print("[bridge] waiting for SITL heartbeat …", flush=True)
    conn.wait_heartbeat()
    print(f"[bridge] got heartbeat from sys={conn.target_system} comp={conn.target_component}", flush=True)

    threading.Thread(target=read_loop, args=(conn,), daemon=True).start()
    threading.Thread(target=publish_loop, args=(client,), daemon=True).start()

    if UPLOAD_MISSION:
        threading.Thread(target=ap_mission.run_mission_loop,
                         args=(conn, HOME_LAT, HOME_LON), daemon=True).start()

    # Park the main thread
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
