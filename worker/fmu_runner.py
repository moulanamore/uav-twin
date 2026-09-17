"""
fmu_runner — thin wrapper around fmpy for the UAVPropulsion FMU.

Loads models/UAVPropulsion.fmu, steps it at each telemetry tick with the
measured currents as inputs, and returns the twin's belief of internal state
(SoC, SoH, per-motor temperature, per-motor bearing damage, etc.).

If the FMU can't be loaded — file missing, incompatible platform, fmpy not
installed — we transparently fall back to a pure-Python implementation of
the same equations so the twin never goes dark.

License: Apache-2.0
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Pure-Python fallback (same physics as Phase 1a)
# ---------------------------------------------------------------------------
class _PythonFallback:
    """Analytical model, same shape as the FMU. Used when FMU is unavailable."""
    Q_NOM_AH        = 5.0
    R_INT_BASE      = 0.020
    R_INT_SLOPE     = 0.005
    N_CELLS         = 4
    V_CELL_EMPTY    = 3.30
    V_CELL_RANGE    = 0.90
    SOH_WEAR_PERAH  = 1.0e-4
    R_MOTOR         = 0.045
    KV              = 900
    C_TH            = 40.0
    H_CONV          = 0.6
    T_AMBIENT       = 30.0
    P_IDLE_FRAC     = 0.05
    BEARING_RATE_REF= 5.55e-7
    I_REF           = 8.0
    I_EXP           = 2.5
    T_ARR_K         = 0.06
    T_REF           = 45.0

    def __init__(self):
        import math
        self._math = math
        self.state = {
            "SoC": 1.0, "SoH": 1.0, "Q_throughput": 0.0,
            "T_m0": self.T_AMBIENT, "T_m1": self.T_AMBIENT,
            "T_m2": self.T_AMBIENT, "T_m3": self.T_AMBIENT,
            "D_m0": 0.0, "D_m1": 0.0, "D_m2": 0.0, "D_m3": 0.0,
            "V_pack": self.N_CELLS * (self.V_CELL_EMPTY + self.V_CELL_RANGE),
            "V_ocv":  self.N_CELLS * (self.V_CELL_EMPTY + self.V_CELL_RANGE),
        }

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        s = self.state
        exp = self._math.exp
        i_pack = inputs["i_pack"]
        R_int = self.R_INT_BASE + self.R_INT_SLOPE * (1.0 - s["SoC"])
        V_ocv = self.N_CELLS * (self.V_CELL_EMPTY + self.V_CELL_RANGE * s["SoC"] ** 0.9)
        V_pack = V_ocv - i_pack * R_int
        s["V_ocv"], s["V_pack"] = V_ocv, V_pack
        s["SoC"] = max(0.0, s["SoC"] - i_pack / (self.Q_NOM_AH * max(s["SoH"], 0.5) * 3600.0) * dt)
        s["Q_throughput"] += abs(i_pack) * dt / 3600.0
        s["SoH"] = max(0.5, s["SoH"] - self.SOH_WEAR_PERAH * abs(i_pack) * dt / 3600.0)
        for k in range(4):
            i = inputs[f"i_m{k}"]
            T_key, D_key = f"T_m{k}", f"D_m{k}"
            p_loss = i * i * self.R_MOTOR + self.P_IDLE_FRAC * V_pack
            s[T_key] += (p_loss - self.H_CONV * (s[T_key] - self.T_AMBIENT)) * dt / self.C_TH
            s[D_key] = min(1.0, s[D_key] + self.BEARING_RATE_REF *
                           (max(0.1, abs(i)) / self.I_REF) ** self.I_EXP *
                           exp(self.T_ARR_K * (s[T_key] - self.T_REF)) * dt)
        return dict(s)


# ---------------------------------------------------------------------------
# FMU-backed runner
# ---------------------------------------------------------------------------
class FMURunner:
    """FMI 2.0 co-simulation wrapper. Falls back to Python if load fails."""

    INPUT_NAMES  = ["i_pack", "i_m0", "i_m1", "i_m2", "i_m3"]
    OUTPUT_NAMES = ["V_pack", "V_ocv", "SoC", "SoH", "Q_throughput",
                    "T_m0", "T_m1", "T_m2", "T_m3",
                    "D_m0", "D_m1", "D_m2", "D_m3",
                    "rpm_m0", "rpm_m1", "rpm_m2", "rpm_m3"]

    def __init__(self, fmu_path: str = "/app/models/UAVPropulsion.fmu"):
        self.mode = "unknown"
        self.fmu = None
        self._unzip = None
        self._sim_time = 0.0
        try:
            self._load_fmu(fmu_path)
            self.mode = "fmu"
            print(f"[fmu_runner] loaded FMU from {fmu_path}", flush=True)
        except Exception as e:
            print(f"[fmu_runner] FMU load failed ({e}); using Python fallback",
                  flush=True)
            self.mode = "python"
            self._fallback = _PythonFallback()

    def _load_fmu(self, fmu_path: str):
        if not os.path.exists(fmu_path):
            raise FileNotFoundError(fmu_path)
        from fmpy import read_model_description, extract
        from fmpy.fmi2 import FMU2Slave
        self._md = read_model_description(fmu_path)
        self._unzip = extract(fmu_path)
        self._fmu = FMU2Slave(guid=self._md.guid, unzipDirectory=self._unzip,
                              modelIdentifier=self._md.coSimulation.modelIdentifier,
                              instanceName="uav_twin")
        self._fmu.instantiate()
        self._fmu.setupExperiment()
        self._fmu.enterInitializationMode()
        self._fmu.exitInitializationMode()
        self._vr = {v.name: v.valueReference for v in self._md.modelVariables}
        # cache the VR lists for fast setReal/getReal
        self._in_vrs  = [self._vr[n] for n in self.INPUT_NAMES]
        self._out_vrs = [self._vr[n] for n in self.OUTPUT_NAMES]

    def step(self, dt: float, inputs: dict[str, float]) -> dict[str, float]:
        """Advance the model by dt seconds, given the input dict, return outputs."""
        if self.mode == "python":
            return self._fallback.step(dt, inputs)
        self._fmu.setReal(self._in_vrs, [float(inputs[n]) for n in self.INPUT_NAMES])
        self._fmu.doStep(currentCommunicationPoint=self._sim_time,
                         communicationStepSize=dt)
        self._sim_time += dt
        vals = self._fmu.getReal(self._out_vrs)
        return dict(zip(self.OUTPUT_NAMES, vals))

    def close(self):
        if self.mode == "fmu" and self._fmu is not None:
            try:
                self._fmu.terminate()
                self._fmu.freeInstance()
            except Exception:
                pass
            if self._unzip:
                shutil.rmtree(self._unzip, ignore_errors=True)


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    r = FMURunner()
    print(f"mode: {r.mode}")
    dt = 0.1
    for i in range(600):
        r.step(dt, dict(i_pack=32.0, i_m0=8.0, i_m1=8.0, i_m2=8.0, i_m3=8.0))
    final = r.step(dt, dict(i_pack=32.0, i_m0=8.0, i_m1=8.0, i_m2=8.0, i_m3=8.0))
    print(f"after 60 s hover: SoC={final['SoC']:.4f} T_m0={final['T_m0']:.2f} "
          f"V_pack={final['V_pack']:.3f}")
    r.close()
