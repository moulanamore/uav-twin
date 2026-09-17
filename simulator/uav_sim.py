"""
Telemetry simulator for a 4-motor quadrotor UAV.

Publishes per-motor and per-cell telemetry to MQTT as if it were a real
vehicle. Runs a scripted mission (takeoff -> climb -> cruise -> hover ->
descent -> landed) on a loop.

Accepts fault-injection commands on:
    uav/<uav_id>/fault/inject   payload = {"target":"motor|cell","index":0..3,"kind":"efficiency|bearing|cell_short","severity":0..1}

License: Apache-2.0
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import paho.mqtt.client as mqtt


MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
UAV_ID = os.getenv("UAV_ID", "quad-01")
TICK_HZ = float(os.getenv("TICK_HZ", "10"))
MISSION_LOOP = os.getenv("MISSION_LOOP", "true").lower() == "true"

# Topic layout
def t_telemetry(kind: str) -> str:
    return f"uav/{UAV_ID}/telemetry/{kind}"

TOPIC_FAULT_INJECT = f"uav/{UAV_ID}/fault/inject"


# ---------------------------------------------------------------------------
# Mission profile — a simple 5-minute mission on a loop.
# Each entry: (duration_s, target_throttle_0_1, description)
# ---------------------------------------------------------------------------
MISSION = [
    (5.0,  0.00, "idle"),
    (10.0, 0.65, "takeoff"),
    (20.0, 0.55, "climb"),
    (120.0, 0.50, "cruise"),
    (60.0, 0.48, "hover"),
    (30.0, 0.42, "descent"),
    (10.0, 0.60, "flare"),
    (5.0,  0.00, "landed"),
]


# ---------------------------------------------------------------------------
# Physical constants for a small ~2 kg quadrotor with a 4S 5000 mAh battery.
# These are baseline "true" values; the twin worker doesn't know them exactly.
# ---------------------------------------------------------------------------
NUM_MOTORS = 4
NUM_CELLS = 4                  # 4S pack

BATTERY_NOMINAL_V = 3.7 * NUM_CELLS
BATTERY_FULL_V    = 4.2 * NUM_CELLS
BATTERY_EMPTY_V   = 3.3 * NUM_CELLS
BATTERY_CAPACITY_AH = 5.0
BATTERY_INTERNAL_R = 0.020     # ohms, whole pack
CELL_INTERNAL_R    = BATTERY_INTERNAL_R / NUM_CELLS

MOTOR_KV = 900                 # rpm per volt no-load
MOTOR_R  = 0.045               # ohm
MOTOR_I0 = 0.6                 # no-load current, A

# Max hover thrust maps to ~0.5 throttle each motor at nominal voltage.
# We back out per-motor current from a simple thrust/power heuristic.
HOVER_CURRENT_PER_MOTOR_A = 8.0    # A at 0.5 throttle, nominal voltage
MAX_CURRENT_PER_MOTOR_A   = 25.0

AMBIENT_TEMP_C = 30.0          # Chennai default :)
MOTOR_THERMAL_MASS = 40.0      # J/K
MOTOR_HEAT_LOSS_COEFF = 0.6    # W/K to ambient


# ---------------------------------------------------------------------------
# Live per-vehicle state
# ---------------------------------------------------------------------------
@dataclass
class MotorState:
    idx: int
    temp_c: float = AMBIENT_TEMP_C
    accum_ah: float = 0.0
    # Fault state (set by fault injection):
    efficiency_derate: float = 1.0      # 1.0 = healthy; 0.8 = 20% derated
    bearing_drag_w: float = 0.0         # extra friction losses
    dead: bool = False


@dataclass
class CellState:
    idx: int
    shorted: bool = False               # simulate a shorted cell
    soh: float = 1.0                    # state of health 0..1


@dataclass
class UAV:
    soc: float = 1.0                    # 0..1
    battery_temp_c: float = AMBIENT_TEMP_C
    motors: list[MotorState] = field(
        default_factory=lambda: [MotorState(i) for i in range(NUM_MOTORS)]
    )
    cells: list[CellState] = field(
        default_factory=lambda: [CellState(i) for i in range(NUM_CELLS)]
    )
    mission_idx: int = 0
    mission_t: float = 0.0
    mission_loops: int = 0
    flight_hours: float = 0.0


uav = UAV()


# ---------------------------------------------------------------------------
# Physics step. Called at TICK_HZ.
# ---------------------------------------------------------------------------
def battery_ocv(soc: float) -> float:
    # Simple SoC->OCV curve, per pack, roughly matching a Li-ion 4S profile.
    soc = max(0.0, min(1.0, soc))
    v_per_cell = 3.3 + 0.9 * (soc ** 0.9)     # 3.3 empty -> 4.2 full
    live_cells = sum(0 if c.shorted else 1 for c in uav.cells)
    return v_per_cell * max(1, live_cells)


def per_motor_current(throttle: float, v_pack: float, m: MotorState) -> float:
    if m.dead:
        return 0.0
    # Base current scales roughly with throttle^2 (thrust ~ throttle^2 in hover).
    base = HOVER_CURRENT_PER_MOTOR_A * (throttle / 0.5) ** 2
    base = min(base, MAX_CURRENT_PER_MOTOR_A)
    # Derated motor pulls MORE current for same torque -> divide by efficiency.
    base = base / max(0.4, m.efficiency_derate)
    return base


def step(dt: float) -> dict[str, Any]:
    # 1) advance mission
    dur, target_throttle, phase = MISSION[uav.mission_idx]
    uav.mission_t += dt
    if uav.mission_t >= dur:
        uav.mission_idx = (uav.mission_idx + 1) % len(MISSION)
        uav.mission_t = 0.0
        if uav.mission_idx == 0:
            uav.mission_loops += 1
            if not MISSION_LOOP:
                print("Mission complete, exiting.", flush=True)
                raise SystemExit(0)

    # add a bit of gust noise on throttle
    throttle = max(0.0, target_throttle + random.gauss(0.0, 0.015))

    # 2) battery voltage from OCV minus IR drop
    v_ocv = battery_ocv(uav.soc)
    currents = [per_motor_current(throttle, v_ocv, m) for m in uav.motors]
    i_pack = sum(currents)
    r_pack = BATTERY_INTERNAL_R + 0.005 * (1.0 - uav.soc)   # rises as depleted
    v_pack = max(0.0, v_ocv - i_pack * r_pack)

    # 3) update SoC (Coulomb count) and per-motor state
    dq_ah = i_pack * dt / 3600.0
    uav.soc = max(0.0, uav.soc - dq_ah / BATTERY_CAPACITY_AH)

    # SoH degrades with cumulative Ah throughput; per-cell health drifts apart.
    for c in uav.cells:
        c.soh = max(0.6, c.soh - 1e-8 * dq_ah)   # tiny per-tick

    # Per-motor thermal + Ah
    for m, i in zip(uav.motors, currents):
        # copper losses + no-load + fault bearing drag
        p_loss = (i * i) * MOTOR_R + (MOTOR_I0 * v_pack / NUM_MOTORS) * 0.1 + m.bearing_drag_w
        # net heat = losses - convection to ambient
        dq = (p_loss - MOTOR_HEAT_LOSS_COEFF * (m.temp_c - AMBIENT_TEMP_C)) * dt
        m.temp_c += dq / MOTOR_THERMAL_MASS
        m.accum_ah += i * dt / 3600.0

    # Battery temperature slowly follows heat from I^2R
    p_batt_loss = i_pack * i_pack * r_pack
    dq_batt = (p_batt_loss - 1.5 * (uav.battery_temp_c - AMBIENT_TEMP_C)) * dt
    uav.battery_temp_c += dq_batt / 800.0

    # Refill SoC if we've landed for a while — pretend a battery swap
    if phase == "landed" and uav.mission_t > 3.0 and uav.soc < 0.98:
        uav.soc = 1.0
        for m in uav.motors:
            m.temp_c = AMBIENT_TEMP_C
        uav.battery_temp_c = AMBIENT_TEMP_C

    # Track flight hours (anything above idle)
    if phase not in ("idle", "landed"):
        uav.flight_hours += dt / 3600.0

    # 4) build telemetry payload
    per_cell_v = (v_pack / NUM_CELLS) if NUM_CELLS > 0 else 0.0
    cells = []
    for c in uav.cells:
        if c.shorted:
            cells.append({"idx": c.idx, "v": 0.0, "healthy": False})
        else:
            # inject some measurement noise + per-cell offset
            v = per_cell_v * (0.98 + 0.04 * c.soh) + random.gauss(0, 0.005)
            cells.append({"idx": c.idx, "v": round(v, 4), "healthy": True})

    motors = []
    for m, i in zip(uav.motors, currents):
        # ESC PWM-averages the pack voltage by throttle command; the motor
        # speed then follows back-emf balance. At throttle=0 the motor coasts
        # to rest, so we anchor RPM to throttle rather than pack voltage.
        v_effective = max(0.0, throttle * v_pack - i * MOTOR_R)
        rpm = max(0.0, v_effective * MOTOR_KV * m.efficiency_derate)
        if m.dead:
            rpm = 0.0
        motors.append({
            "idx": m.idx,
            "rpm": round(rpm, 1),
            "current_a": round(i, 3),
            "voltage_v": round(v_pack, 3),
            "temp_c": round(m.temp_c, 2),
            "accum_ah": round(m.accum_ah, 4),
        })

    return {
        "ts": time.time(),
        "uav_id": UAV_ID,
        "phase": phase,
        "throttle": round(throttle, 3),
        "battery": {
            "pack_v": round(v_pack, 3),
            "pack_i_a": round(i_pack, 3),
            "soc": round(uav.soc, 4),
            "temp_c": round(uav.battery_temp_c, 2),
            "cells": cells,
        },
        "motors": motors,
        "flight_hours": round(uav.flight_hours, 4),
        "mission_loops": uav.mission_loops,
    }


# ---------------------------------------------------------------------------
# Fault injection via MQTT
# ---------------------------------------------------------------------------
def on_fault(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"[sim] bad fault payload: {e}", flush=True)
        return
    target = payload.get("target")
    idx = int(payload.get("index", 0))
    kind = payload.get("kind")
    sev  = float(payload.get("severity", 0.5))
    print(f"[sim] FAULT INJECT target={target} idx={idx} kind={kind} sev={sev}", flush=True)

    if target == "motor" and 0 <= idx < NUM_MOTORS:
        m = uav.motors[idx]
        if kind == "efficiency":
            m.efficiency_derate = max(0.4, 1.0 - sev)
        elif kind == "bearing":
            m.bearing_drag_w = 15.0 * sev
        elif kind == "dead":
            m.dead = True
    elif target == "cell" and 0 <= idx < NUM_CELLS:
        c = uav.cells[idx]
        if kind == "cell_short":
            c.shorted = True
        elif kind == "soh_drop":
            c.soh = max(0.5, c.soh - sev)
    elif target == "reset":
        # heal everything
        for m in uav.motors:
            m.efficiency_derate = 1.0
            m.bearing_drag_w = 0.0
            m.dead = False
        for c in uav.cells:
            c.shorted = False
            c.soh = 1.0
        print("[sim] all faults reset", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    client = mqtt.Client(client_id=f"sim-{UAV_ID}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p: (
        print(f"[sim] connected rc={rc}", flush=True),
        c.subscribe(TOPIC_FAULT_INJECT),
    )
    client.message_callback_add(TOPIC_FAULT_INJECT, on_fault)

    print(f"[sim] connecting to mqtt://{MQTT_HOST}:{MQTT_PORT} …", flush=True)
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except Exception as e:
            print(f"[sim] mqtt not ready ({e}), retrying …", flush=True)
            time.sleep(2)

    client.loop_start()

    dt = 1.0 / TICK_HZ
    next_tick = time.monotonic()
    while True:
        payload = step(dt)
        client.publish(t_telemetry("state"), json.dumps(payload), qos=0)
        next_tick += dt
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_tick = time.monotonic()   # we slipped, resync


if __name__ == "__main__":
    main()
