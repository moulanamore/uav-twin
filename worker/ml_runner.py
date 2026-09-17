# -*- coding: utf-8 -*-
"""
ml_runner -- Phase 3 ML layer wrapper.

Loads two models saved by notebooks 04 and 05:
  * bearing_wear.pkl -- XGBoost motor-anomaly classifier (Model A)
  * battery_rul.pkl  -- Gaussian process battery RUL predictor (Model B)

Falls back to safe no-op predictions if either model fails to load, so the
worker never goes dark. Mirrors the design of fmu_runner.FMURunner.

Pure-ASCII source. License: Apache-2.0.
"""

from __future__ import annotations

import json
import os
import pathlib
import pickle
from typing import Any

import numpy as np


class MLRunner:
    def __init__(self, models_dir: str = "/app/models"):
        self.models_dir = pathlib.Path(models_dir)
        self.mode = {"model_a": "unavailable", "model_b": "unavailable"}
        self._model_a = None
        self._gp_b = None
        self._load_model_a()
        self._load_model_b()
        print("[ml_runner] modes: {}".format(self.mode), flush=True)

    def _load_model_a(self):
        pkl = self.models_dir / "bearing_wear.pkl"
        cfg = self.models_dir / "bearing_wear.json"
        try:
            with open(pkl, "rb") as f:
                self._model_a = pickle.load(f)
            with open(cfg) as f:
                self._cfg_a = json.load(f)
            self._features_a = self._cfg_a["feature_order"]
            self._threshold_a = self._cfg_a["operational_threshold"]
            self.mode["model_a"] = "xgboost"
            print("[ml_runner] loaded bearing_wear.pkl (xgboost, {} features)".format(
                len(self._features_a)), flush=True)
        except Exception as e:
            print("[ml_runner] Model A load failed: {}".format(e), flush=True)

    def _load_model_b(self):
        pkl = self.models_dir / "battery_rul.pkl"
        cfg = self.models_dir / "battery_rul.json"
        try:
            with open(pkl, "rb") as f:
                obj = pickle.load(f)
            self._scaler_b = obj["scaler"]
            self._gp_b = obj["gp"]
            with open(cfg) as f:
                self._cfg_b = json.load(f)
            self._features_b = self._cfg_b["feature_order"]
            self.mode["model_b"] = "gp"
            print("[ml_runner] loaded battery_rul.pkl (gp, {} features)".format(
                len(self._features_b)), flush=True)
        except Exception as e:
            print("[ml_runner] Model B load failed: {}".format(e), flush=True)

    def motor_probability(self, features: dict) -> tuple[float, bool]:
        """Return (P(fault), alert_bool) given a 2-second window feature dict."""
        if self._model_a is None:
            return 0.0, False
        try:
            x = np.array([[features[k] for k in self._features_a]])
            p = float(self._model_a.predict_proba(x)[0, 1])
            return p, bool(p >= self._threshold_a)
        except Exception as e:
            print("[ml_runner] motor_probability error: {}".format(e), flush=True)
            return 0.0, False

    def battery_rul(self, features: dict) -> dict:
        """Return {median, p05, p95, unavailable} flights."""
        if self._gp_b is None:
            return {"median": -1, "p05": -1, "p95": -1, "unavailable": True}
        try:
            x = np.array([[features[k] for k in self._features_b]])
            x_scaled = self._scaler_b.transform(x)
            y_log, y_std = self._gp_b.predict(x_scaled, return_std=True)
            return {
                "median": float(np.expm1(y_log[0])),
                "p05":    float(np.expm1(y_log[0] - 1.645 * y_std[0])),
                "p95":    float(np.expm1(y_log[0] + 1.645 * y_std[0])),
                "unavailable": False,
            }
        except Exception as e:
            print("[ml_runner] battery_rul error: {}".format(e), flush=True)
            return {"median": -1, "p05": -1, "p95": -1, "unavailable": True}


def extract_motor_features(telemetry_buffer: list[dict],
                           num_motors: int = 4,
                           window_s: float = 2.0) -> dict:
    """Extract the 28-feature vector Model A expects from a rolling telemetry
    buffer. telemetry_buffer is a list of MQTT telemetry/state payloads.
    Matches alfa_loader.flight_features exactly so training and inference
    stay schema-identical.
    """
    if not telemetry_buffer:
        return {}
    features = {}
    mean_is = []
    for m in range(num_motors):
        i_arr    = np.array([t["motors"][m]["current_a"] for t in telemetry_buffer])
        temp_arr = np.array([t["motors"][m]["temp_c"]    for t in telemetry_buffer])
        rpm_arr  = np.array([t["motors"][m]["rpm"]       for t in telemetry_buffer])
        features["m{}_mean_i".format(m)]     = float(i_arr.mean())
        features["m{}_rms_i".format(m)]      = float(np.sqrt((i_arr ** 2).mean()))
        features["m{}_std_i".format(m)]      = float(i_arr.std())
        features["m{}_peak_i".format(m)]     = float(np.abs(i_arr).max())
        features["m{}_mean_temp".format(m)]  = float(temp_arr.mean())
        features["m{}_temp_slope".format(m)] = float((temp_arr[-1] - temp_arr[0]) / window_s)
        mean_is.append(features["m{}_mean_i".format(m)])
    mean_is = np.array(mean_is)
    features["cross_max_asym"]      = float(mean_is.max() - mean_is.min())
    features["cross_i_var"]         = float(mean_is.var())
    features["cross_min_rpm"]       = float(min(
        np.mean([t["motors"][m]["rpm"] for t in telemetry_buffer]) for m in range(num_motors)
    ))
    temp_means = [np.mean([t["motors"][m]["temp_c"] for t in telemetry_buffer])
                  for m in range(num_motors)]
    features["cross_max_temp_diff"] = float(max(temp_means) - min(temp_means))
    return features
