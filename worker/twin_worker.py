# -*- coding: utf-8 -*-
"""
twin_worker -- Phase 3c integrated worker.

Fuses three engines behind one MQTT/InfluxDB shell:
  1. FMURunner  : OpenModelica physics (SoC, SoH, temps, bearing damage)
  2. MLRunner A : XGBoost motor-bearing anomaly classifier
  3. MLRunner B : Gaussian process battery RUL predictor

Topic convention (matches bridge):
  IN  : uav/<uav_id>/telemetry/state    (from bridge / simulator)
  OUT : uav/<uav_id>/twin/state         (~10 Hz, full fused state)
        uav/<uav_id>/twin/anomaly       (~2 s, motor fault probability)
        uav/<uav_id>/twin/rul           (~30 s, battery RUL flights)
        uav/<uav_id>/twin/alerts        (only when a threshold trips)

Also writes InfluxDB points for the historian.

Pure-ASCII source. License: Apache-2.0.
"""

from __future__ import annotations

import collections
import json
import os
import signal
import sys
import time

import paho.mqtt.client as mqtt
import numpy as np

from fmu_runner import FMURunner
from ml_runner import MLRunner, extract_motor_features


# ------------------------------------------------------------- configuration --
MQTT_HOST     = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT     = int(os.environ.get("MQTT_PORT", "1883"))
INFLUX_URL    = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN  = os.environ.get("INFLUX_TOKEN", "uavtwin-dev-token")
INFLUX_ORG    = os.environ.get("INFLUX_ORG", "uavtwin")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "telemetry")
UAV_ID        = os.environ.get("UAV_ID", "quad-01")

TELEMETRY_TOPIC = "uav/{}/telemetry/state".format(UAV_ID)
TWIN_STATE      = "uav/{}/twin/state".format(UAV_ID)
TWIN_ANOMALY    = "uav/{}/twin/anomaly".format(UAV_ID)
TWIN_RUL        = "uav/{}/twin/rul".format(UAV_ID)
TWIN_ALERTS     = "uav/{}/twin/alerts".format(UAV_ID)

NUM_MOTORS         = 4
MOTOR_BUF_SECONDS  = 2.0        # rolling window for XGBoost features
RUL_EVERY_SECONDS  = 30.0       # cadence for GP RUL forecast
FLIGHT_AH          = 1.0        # equivalent Ah per flight (empirical)
MIN_FLIGHTS_FOR_GP = 5          # need history before GP is meaningful
CELL_SPREAD_LIMIT  = 8.0        # deg C spread across motors flagged as alert
MOTOR_ASYM_LIMIT   = 3.5        # amps cross-motor imbalance flagged as alert

# ------------------------------------------------------------- influx client --
try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    _influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    _write_api = _influx.write_api(write_options=SYNCHRONOUS)
    INFLUX_MODE = "ok"
except Exception as exc:
    print("[worker] influx client init failed: {}".format(exc), flush=True)
    _write_api = None
    Point = None
    WritePrecision = None
    INFLUX_MODE = "unavailable"


# ------------------------------------------------------------ rolling state --
class _RollingBuffer:
    """Keep the last N seconds of telemetry samples for feature extraction."""
    def __init__(self, seconds: float, tick_hz_hint: float = 10.0):
        maxlen = max(4, int(seconds * tick_hz_hint * 1.5))
        self.buf = collections.deque(maxlen=maxlen)
        self.window_s = seconds

    def push(self, sample: dict):
        self.buf.append(sample)

    def as_list(self) -> list[dict]:
        return list(self.buf)

    def ready(self, min_samples: int = 8) -> bool:
        return len(self.buf) >= min_samples


class _FlightHistory:
    """Track per-flight battery snapshots for GP feature extraction."""
    def __init__(self):
        self.snapshots: list[dict] = []
        self._last_ah = 0.0

    def maybe_snapshot(self, fmu_state: dict, motor_temps: list[float]):
        """Record a snapshot at every full flight equivalent of Ah throughput."""
        ah = float(fmu_state.get("Q_throughput", 0.0))
        if ah - self._last_ah < FLIGHT_AH:
            return False
        self._last_ah = ah
        soh = float(fmu_state.get("SoH", 1.0))
        v_ocv = float(fmu_state.get("V_ocv", 15.0))
        v_pack = float(fmu_state.get("V_pack", 15.0))
        # crude R_int estimate from voltage sag; safe defaults if divide-by-zero
        r_int = 0.020
        try:
            i_pack = float(fmu_state.get("_i_pack", 0.0))
            if abs(i_pack) > 0.5:
                r_int = max(0.005, (v_ocv - v_pack) / i_pack)
        except Exception:
            pass
        self.snapshots.append({
            "flight": len(self.snapshots) + 1,
            "soh": soh,
            "r_int": r_int,
            "avg_motor_temp": float(np.mean(motor_temps)) if motor_temps else 30.0,
            "ah_throughput": ah,
        })
        # keep last 60 flights
        self.snapshots = self.snapshots[-60:]
        return True

    def features(self, window: int = 5) -> dict:
        """Compose battery feature dict for MLRunner.battery_rul."""
        if len(self.snapshots) < 2:
            return {}
        recent = self.snapshots[-window:]
        soh_arr = np.array([s["soh"] for s in recent])
        r_arr   = np.array([s["r_int"] for s in recent])
        t_arr   = np.array([s["avg_motor_temp"] for s in recent])
        last = self.snapshots[-1]
        idx = np.arange(len(recent), dtype=float)
        def slope(y):
            if len(y) < 2:
                return 0.0
            m = np.polyfit(idx, y, 1)[0]
            return float(m)
        # Feature names MUST match battery_rul.json:feature_order exactly, or
        # ml_runner.battery_rul() will KeyError and return unavailable.
        # Model B expects: cycles_elapsed, soh, internal_r_ohm, peak_temp_c,
        #                  capacity_slope, r_slope.  cycles_per_flight = 0.2
        #                  (from notebook 03 / battery_rul.json).
        cycles_per_flight = 0.2
        return {
            "cycles_elapsed":   float(len(self.snapshots) * cycles_per_flight),
            "soh":              float(last["soh"]),
            "internal_r_ohm":   float(last["r_int"]),
            "peak_temp_c":      float(last["avg_motor_temp"]),
            "capacity_slope":   slope(soh_arr),
            "r_slope":          slope(r_arr),
        }


# --------------------------------------------------------------- twin core --
class TwinCore:
    def __init__(self):
        self.fmu = FMURunner()
        self.ml  = MLRunner()
        self.motor_buf = _RollingBuffer(MOTOR_BUF_SECONDS)
        self.flights   = _FlightHistory()
        self._last_tick = time.time()
        self._last_motor_pub = 0.0
        self._last_rul_pub = 0.0
        self._msg_count = 0
        self.mqtt = mqtt.Client(client_id="uavtwin-worker",
                                clean_session=True)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message

    # -- MQTT plumbing --------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        rc_name = mqtt.error_string(rc)
        print("[worker] connected rc={} (physics={}, ml={})".format(
            rc_name, self.fmu.mode, self.ml.mode), flush=True)
        client.subscribe(TELEMETRY_TOPIC, qos=0)
        print("[worker] subscribed to {}".format(TELEMETRY_TOPIC), flush=True)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print("[worker] bad payload: {}".format(exc), flush=True)
            return
        self._handle_tick(payload)

    # -- per-tick handler -----------------------------------------------------
    def _handle_tick(self, sample: dict):
        now = time.time()
        dt = max(0.01, now - self._last_tick)
        self._last_tick = now
        self._msg_count += 1
        if self._msg_count == 1:
            print("[worker] first telemetry sample received", flush=True)

        # 1. Step FMU with measured currents from telemetry
        motors = sample.get("motors", [])
        if len(motors) < NUM_MOTORS:
            return
        i_m = [float(motors[k].get("current_a", 0.0)) for k in range(NUM_MOTORS)]
        batt = sample.get("battery", {})
        # bridge uses pack_i_a; simulator (Phase 1a) used current_a; fall back to sum
        i_pack = float(batt.get("pack_i_a",
                                batt.get("current_a", sum(i_m))))
        inputs = {"i_pack": i_pack,
                  "i_m0": i_m[0], "i_m1": i_m[1],
                  "i_m2": i_m[2], "i_m3": i_m[3]}
        fmu_state = self.fmu.step(dt, inputs)
        fmu_state["_i_pack"] = i_pack

        # 2. Publish fused twin state
        motor_temps = [float(fmu_state.get("T_m{}".format(k), 30.0))
                       for k in range(NUM_MOTORS)]
        motor_damage = [float(fmu_state.get("D_m{}".format(k), 0.0))
                        for k in range(NUM_MOTORS)]
        twin_state = {
            "ts": now,
            "uav_id": UAV_ID,
            "physics": self.fmu.mode,
            "SoC": float(fmu_state.get("SoC", 1.0)),
            "SoH": float(fmu_state.get("SoH", 1.0)),
            "V_pack": float(fmu_state.get("V_pack", 15.0)),
            "V_ocv":  float(fmu_state.get("V_ocv", 15.0)),
            "Q_throughput": float(fmu_state.get("Q_throughput", 0.0)),
            "motor_temps_c": motor_temps,
            "motor_damage":  motor_damage,
        }
        self.mqtt.publish(TWIN_STATE, json.dumps(twin_state), qos=0)
        self._write_influx(twin_state, i_pack, i_m)

        # 3. Roll motor buffer for XGBoost
        self.motor_buf.push(sample)

        # 4. Every MOTOR_BUF_SECONDS, run XGBoost + emit anomaly + alerts
        if now - self._last_motor_pub >= MOTOR_BUF_SECONDS and self.motor_buf.ready():
            self._last_motor_pub = now
            self._emit_anomaly(now, motor_temps, i_m)

        # 5. Track battery flight snapshots; every RUL_EVERY_SECONDS emit RUL
        self.flights.maybe_snapshot(fmu_state, motor_temps)
        if now - self._last_rul_pub >= RUL_EVERY_SECONDS:
            self._last_rul_pub = now
            self._emit_rul(now)

    # -- XGBoost + alerts -----------------------------------------------------
    def _emit_anomaly(self, now: float, motor_temps: list[float], i_m: list[float]):
        try:
            feats = extract_motor_features(self.motor_buf.as_list(),
                                           num_motors=NUM_MOTORS,
                                           window_s=MOTOR_BUF_SECONDS)
        except Exception as exc:
            print("[worker] motor feature extract failed: {}".format(exc),
                  flush=True)
            return
        if not feats:
            return
        p_fault, alert = self.ml.motor_probability(feats)
        payload = {"ts": now, "uav_id": UAV_ID,
                   "p_fault": p_fault, "alert": alert,
                   "cross_max_asym": feats.get("cross_max_asym", 0.0),
                   "cross_max_temp_diff": feats.get("cross_max_temp_diff", 0.0),
                   "mode": self.ml.mode["model_a"]}
        self.mqtt.publish(TWIN_ANOMALY, json.dumps(payload), qos=0)
        alerts = []
        if feats.get("cross_max_temp_diff", 0.0) > CELL_SPREAD_LIMIT:
            alerts.append({"kind": "motor_temp_spread",
                           "value": feats["cross_max_temp_diff"],
                           "limit": CELL_SPREAD_LIMIT})
        if feats.get("cross_max_asym", 0.0) > MOTOR_ASYM_LIMIT:
            alerts.append({"kind": "motor_current_asym",
                           "value": feats["cross_max_asym"],
                           "limit": MOTOR_ASYM_LIMIT})
        if alert:
            alerts.append({"kind": "ml_bearing_wear",
                           "value": p_fault,
                           "limit": 0.5})
        if alerts:
            self.mqtt.publish(TWIN_ALERTS,
                              json.dumps({"ts": now, "uav_id": UAV_ID,
                                          "alerts": alerts}), qos=0)

    # -- GP RUL ---------------------------------------------------------------
    def _emit_rul(self, now: float):
        feats = self.flights.features()
        payload = {"ts": now, "uav_id": UAV_ID,
                   "mode": self.ml.mode["model_b"],
                   "flights_observed": len(self.flights.snapshots)}
        if len(self.flights.snapshots) < MIN_FLIGHTS_FOR_GP or not feats:
            payload.update({"median": -1, "p05": -1, "p95": -1,
                            "unavailable": True,
                            "reason": "warming_up"})
        else:
            rul = self.ml.battery_rul(feats)
            payload.update(rul)
        self.mqtt.publish(TWIN_RUL, json.dumps(payload), qos=0)

    # -- InfluxDB historian ---------------------------------------------------
    def _write_influx(self, state: dict, i_pack: float, i_m: list[float]):
        if _write_api is None or Point is None:
            return
        try:
            pt = (Point("twin_state")
                  .tag("uav_id", UAV_ID)
                  .tag("physics", state["physics"])
                  .field("SoC", state["SoC"])
                  .field("SoH", state["SoH"])
                  .field("V_pack", state["V_pack"])
                  .field("V_ocv",  state["V_ocv"])
                  .field("Q_throughput", state["Q_throughput"])
                  .field("i_pack", float(i_pack)))
            for k in range(NUM_MOTORS):
                pt = (pt.field("T_m{}".format(k), state["motor_temps_c"][k])
                        .field("D_m{}".format(k), state["motor_damage"][k])
                        .field("i_m{}".format(k), float(i_m[k])))
            _write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=pt)
        except Exception as exc:
            print("[worker] influx write error: {}".format(exc), flush=True)

    # -- lifecycle ------------------------------------------------------------
    def run(self):
        self.mqtt.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.mqtt.loop_forever()

    def shutdown(self, *args):
        print("[worker] shutdown requested", flush=True)
        try:
            self.mqtt.disconnect()
        except Exception:
            pass
        try:
            self.fmu.close()
        except Exception:
            pass
        sys.exit(0)


# ---------------------------------------------------------------------- main --
def main():
    print("[worker] starting UAV_ID={} MQTT={}:{} influx={}".format(
        UAV_ID, MQTT_HOST, MQTT_PORT, INFLUX_MODE), flush=True)
    print("[worker] telemetry topic: {}".format(TELEMETRY_TOPIC), flush=True)
    core = TwinCore()
    signal.signal(signal.SIGTERM, core.shutdown)
    signal.signal(signal.SIGINT,  core.shutdown)
    core.run()


if __name__ == "__main__":
    main()
