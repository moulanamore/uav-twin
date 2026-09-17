"""
ALFA UAV fault-flight loader — with a synthetic fallback so the notebook
runs end-to-end even before the real 2 GB ROS-bag dataset is downloaded.

ALFA = A dataset for UAV fault and anomaly detection (CMU AirLab).
    Paper: Keipour, Mousaei & Scherer, 2019 (arXiv:1907.06268)
    Repo:  https://github.com/castacks/alfa-dataset
    ~47 real fixed-wing UAV flights, 8 fault classes with millisecond-
    accurate onset/recovery timestamps.

The dataset ships as ROS bag files. For our purposes (multirotor motor
prognostics), the interesting ALFA classes are the ENGINE_FAILURE
scenarios — those translate directly to a rotor-motor failure on a
multirotor. We adapt the feature space to per-motor ESC telemetry
(current, RPM, temperature) at 10 Hz, matching what our MAVLink bridge
produces on the twin's MQTT bus.

License: Apache-2.0
"""

from __future__ import annotations

import os
import pathlib
import numpy as np
import pandas as pd


ALFA_ROOT_ENV = "ALFA_DATA_ROOT"
TELEM_HZ      = 10          # 10 Hz — matches our MAVLink bridge cadence
FLIGHT_SEC    = 300         # 5-minute flights, typical UAV sortie
NUM_MOTORS    = 4


def real_data_root() -> pathlib.Path | None:
    root = os.environ.get(ALFA_ROOT_ENV)
    if root and pathlib.Path(root).exists():
        return pathlib.Path(root)
    return None


# ---------------------------------------------------------------------------
# Synthetic multirotor flight generator
# ---------------------------------------------------------------------------
def _mission_throttle(t: np.ndarray) -> np.ndarray:
    """5-minute mission: takeoff, cruise-hover, descent, land."""
    throttle = np.zeros_like(t)
    # 0-20 s: takeoff ramp
    m = t < 20
    throttle[m] = 0.30 + 0.35 * (t[m] / 20)
    # 20-240 s: cruise/hover ~0.55
    m = (t >= 20) & (t < 240)
    throttle[m] = 0.55 + 0.02 * np.sin(2 * np.pi * t[m] / 15)  # gentle sway
    # 240-280 s: descent 0.55 → 0.35
    m = (t >= 240) & (t < 280)
    throttle[m] = 0.55 - 0.20 * (t[m] - 240) / 40
    # 280-300 s: flare + land
    m = t >= 280
    throttle[m] = 0.35 - 0.35 * (t[m] - 280) / 20
    return np.clip(throttle, 0.0, 1.0)


def _one_flight(flight_id: int, has_fault: bool,
                rng: np.random.Generator) -> pd.DataFrame:
    """
    Produce per-tick, per-motor telemetry for one flight.
    Columns: t, motor_idx, current_a, rpm, temp_c, throttle, fault_flag
    """
    n_ticks = FLIGHT_SEC * TELEM_HZ
    t = np.arange(n_ticks) / TELEM_HZ
    throttle = _mission_throttle(t)

    # Base per-motor current: hover ≈ 8 A / motor, roughly (throttle/0.5)²
    base_i = 8.0 * (throttle / 0.5) ** 2
    base_i = np.clip(base_i, 0.05, 25.0)

    # Nominal RPM tracks throttle × pack_v × kv
    pack_v = 16.0            # constant-ish for the flight
    kv     = 900
    base_rpm = throttle * pack_v * kv

    # Optional fault injection: pick a motor, pick an onset time in cruise
    if has_fault:
        fault_motor = int(rng.integers(0, NUM_MOTORS))
        fault_onset_s = float(rng.uniform(60, 200))  # somewhere in cruise
        fault_type    = rng.choice(["dead", "efficiency", "bearing"])
    else:
        fault_motor, fault_onset_s, fault_type = -1, np.inf, None

    rows = []
    for k in range(NUM_MOTORS):
        # Per-motor noise (measurement + wind gust modulation)
        current_noise = 0.30 * rng.standard_normal(n_ticks)
        rpm_noise     = 20   * rng.standard_normal(n_ticks)
        temp_noise    = 0.15 * rng.standard_normal(n_ticks)

        i_k   = base_i.copy()   + current_noise
        rpm_k = base_rpm.copy() + rpm_noise
        # Thermal: single-mass, quasi-static estimate: T ~ ambient + k*I²
        temp_k = 30.0 + 0.6 * (i_k ** 2) + temp_noise

        # Apply fault if this is the target motor and t > onset
        fault_mask = t >= fault_onset_s
        if has_fault and k == fault_motor and fault_mask.any():
            if fault_type == "dead":
                i_k[fault_mask]  = 0.0
                rpm_k[fault_mask] = 0.0
                temp_k[fault_mask] -= (temp_k[fault_mask] - 30.0) * 0.4
            elif fault_type == "efficiency":
                # 40% efficiency loss → 66% higher current for same work
                i_k[fault_mask] *= 1.66
                temp_k[fault_mask] += 5.0 * ((t[fault_mask] - fault_onset_s) / 30).clip(max=1.0)
            elif fault_type == "bearing":
                # bearing drag: extra 4W dissipation, ripple current
                ripple = 1.5 * np.sin(2 * np.pi * 12 * t[fault_mask])
                i_k[fault_mask] += 1.2 + ripple
                temp_k[fault_mask] += 3.0

        # Other motors compensate slightly for lost thrust when one is dead
        if has_fault and fault_type == "dead" and k != fault_motor:
            comp_mask = t >= fault_onset_s
            i_k[comp_mask] *= 1.15
            temp_k[comp_mask] += 2.0

        df = pd.DataFrame({
            "t":         t,
            "motor_idx": k,
            "current_a": i_k,
            "rpm":       rpm_k,
            "temp_c":    temp_k,
            "throttle":  throttle,
            "fault_flag": (has_fault & (k == fault_motor) &
                           (t >= fault_onset_s)).astype(int),
        })
        rows.append(df)

    out = pd.concat(rows, ignore_index=True)
    out.attrs.update({
        "flight_id":     flight_id,
        "fault_motor":   fault_motor,
        "fault_onset_s": fault_onset_s if has_fault else None,
        "fault_type":    fault_type,
        "source":        "synthetic",
    })
    return out


def load_flights(n_flights: int = 20,
                 fault_fraction: float = 0.6,
                 seed: int = 42,
                 source: str = "auto") -> list[pd.DataFrame]:
    """Return a list of per-flight DataFrames."""
    if source == "real" or (source == "auto" and real_data_root() is not None):
        return _load_real(n_flights)
    rng = np.random.default_rng(seed)
    flights = []
    for i in range(n_flights):
        has_fault = rng.random() < fault_fraction
        flights.append(_one_flight(i, has_fault, rng))
    return flights


def _load_real(n_flights: int) -> list[pd.DataFrame]:
    root = real_data_root()
    if root is None:
        raise FileNotFoundError(
            f"Real ALFA dataset not found. Set {ALFA_ROOT_ENV} to a folder "
            f"of parsed CSV flights (see notebook 02 README).")
    # Assumes the user has run rosbag→csv preprocessing per ALFA repo
    # instructions. Each CSV should carry the columns the synthetic path
    # produces above; if your preprocessor names them differently, remap
    # here.
    files = sorted(root.glob("*.csv"))[:n_flights]
    flights = []
    for f in files:
        df = pd.read_csv(f)
        df.attrs["source"] = "real"
        flights.append(df)
    return flights


# ---------------------------------------------------------------------------
# Feature extraction on rolling windows
# ---------------------------------------------------------------------------
def flight_features(flight: pd.DataFrame,
                    window_s: float = 2.0,
                    stride_s: float = 1.0) -> pd.DataFrame:
    """
    Slide a window across a flight and extract per-window features.
    Every window is a candidate ML input.

    Features:
      Per-motor:  mean_i, rms_i, std_i, peak_i, mean_temp, temp_slope
      Cross-motor: max_asym, i_var, min_rpm, max_temp_diff

    Label: fault_in_window = 1 if fault_flag was set anywhere in the window.
    """
    win_n = int(window_s * TELEM_HZ)
    stride_n = int(stride_s * TELEM_HZ)
    per_motor = {m: flight[flight.motor_idx == m].reset_index(drop=True)
                 for m in range(NUM_MOTORS)}
    n_ticks = len(per_motor[0])

    rows = []
    for start in range(0, n_ticks - win_n + 1, stride_n):
        end = start + win_n
        t_mid = per_motor[0].t.iloc[start + win_n // 2]

        motors_i    = [per_motor[m].current_a.iloc[start:end].values for m in range(NUM_MOTORS)]
        motors_temp = [per_motor[m].temp_c.iloc[start:end].values    for m in range(NUM_MOTORS)]
        motors_rpm  = [per_motor[m].rpm.iloc[start:end].values       for m in range(NUM_MOTORS)]
        fault_flag  = per_motor[0].fault_flag.iloc[start:end].max()
        # (fault_flag lives on the faulted motor, so also check other motors)
        for m in range(1, NUM_MOTORS):
            fault_flag = max(fault_flag, per_motor[m].fault_flag.iloc[start:end].max())

        feats = {"t_mid": t_mid, "fault": int(fault_flag)}
        mean_is = []
        for m in range(NUM_MOTORS):
            i = motors_i[m]
            feats[f"m{m}_mean_i"]     = float(i.mean())
            feats[f"m{m}_rms_i"]      = float(np.sqrt((i**2).mean()))
            feats[f"m{m}_std_i"]      = float(i.std())
            feats[f"m{m}_peak_i"]     = float(np.abs(i).max())
            feats[f"m{m}_mean_temp"]  = float(motors_temp[m].mean())
            feats[f"m{m}_temp_slope"] = float((motors_temp[m][-1] - motors_temp[m][0]) / window_s)
            mean_is.append(feats[f"m{m}_mean_i"])
        # cross-motor
        mean_is = np.array(mean_is)
        feats["cross_max_asym"] = float(mean_is.max() - mean_is.min())
        feats["cross_i_var"]    = float(mean_is.var())
        feats["cross_min_rpm"]  = float(min(np.mean(r) for r in motors_rpm))
        feats["cross_max_temp_diff"] = float(
            max(np.mean(motors_temp[m]) for m in range(NUM_MOTORS)) -
            min(np.mean(motors_temp[m]) for m in range(NUM_MOTORS))
        )
        rows.append(feats)
    return pd.DataFrame(rows)
