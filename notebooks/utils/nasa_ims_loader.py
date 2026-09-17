"""
NASA IMS Bearing dataset loader — with a synthetic fallback so the notebook
runs end-to-end even before the real 5 GB dataset is downloaded.

The IMS dataset comes from the University of Cincinnati's Center for
Intelligent Maintenance Systems and is public domain. Original mirror:
    https://data.nasa.gov/dataset/ims-bearings
    https://www.kaggle.com/datasets/vinayak123tyagi/bearing-dataset
Format:
    - Three test-to-failure experiments
    - Each snapshot is 1 second at 20 480 Hz across N accelerometer channels
    - Filenames are timestamps: YYYY.MM.DD.hh.mm.ss
    - Test 2: 984 files, 4 bearings × 1 channel each, ~7 days total
      (bearing 1 develops an outer-race fault; the run-to-failure signature
       is textbook and small enough to work with quickly).

License: Apache-2.0
"""

from __future__ import annotations

import os
import pathlib
import numpy as np
import pandas as pd


IMS_ROOT_ENV = "IMS_DATA_ROOT"
SAMPLING_HZ  = 20480
SAMPLES_PER_FILE = 20480  # one second at 20.48 kHz


def real_data_root() -> pathlib.Path | None:
    """Return the folder holding the real IMS test-2 files if configured."""
    root = os.environ.get(IMS_ROOT_ENV)
    if root and pathlib.Path(root).exists():
        return pathlib.Path(root)
    return None


# ---------------------------------------------------------------------------
# Synthetic run-to-failure generator
# ---------------------------------------------------------------------------
def _damage_factor(t_norm: float) -> float:
    """
    Damage progression: mostly flat for the first 60% of the test, then
    accelerating exponential wear until end-of-life. This shape matches
    the actual IMS bearing 1 outer-race fault progression.
    """
    if t_norm < 0.60:
        return 0.02 + 0.03 * t_norm
    # accelerating phase
    x = (t_norm - 0.60) / 0.40
    return 0.05 + 0.95 * (x ** 2.3)


def _synth_one_file(t_norm: float, rng: np.random.Generator,
                    fault_freq_hz: float = 236.4,
                    shaft_hz: float = 33.3) -> np.ndarray:
    """
    Synthesize one 1-second vibration snapshot.

    Healthy: broadband noise + weak shaft-frequency tone.
    Progressing outer-race fault: shaft tone unchanged; impulse train at
    the outer-race characteristic frequency grows with damage_factor;
    modulated by exponential decay of each impulse (as the ball rolls
    over the defect).
    """
    n = SAMPLES_PER_FILE
    t = np.arange(n) / SAMPLING_HZ

    # baseline broadband noise (motor + support structure)
    x = 0.15 * rng.standard_normal(n)

    # weak shaft-frequency harmonic (always present)
    x += 0.05 * np.sin(2 * np.pi * shaft_hz * t)
    x += 0.02 * np.sin(2 * np.pi * 2 * shaft_hz * t)

    # progressive impulse train at outer-race characteristic frequency
    damage = _damage_factor(t_norm)
    # each impulse: instantaneous excitation with exponential ringing
    period_s = 1.0 / fault_freq_hz
    ring_freq = 4200.0    # bearing resonance excited by the impulse
    decay_tau = 0.0008    # 0.8 ms decay
    t_impulses = np.arange(0, 1.0, period_s)
    for t_i in t_impulses:
        idx = int(t_i * SAMPLING_HZ)
        if idx >= n:
            break
        # slight jitter in impulse timing (bearing slip)
        idx += int(rng.integers(-3, 4))
        idx = max(0, min(idx, n - 1))
        length = min(int(0.005 * SAMPLING_HZ), n - idx)   # 5 ms of ringing
        env = np.exp(-np.arange(length) / (decay_tau * SAMPLING_HZ))
        ring = np.sin(2 * np.pi * ring_freq * np.arange(length) / SAMPLING_HZ)
        x[idx:idx+length] += damage * 3.5 * env * ring

    # in late stages, structural fatigue → higher random noise floor
    x += damage * 0.4 * rng.standard_normal(n)
    return x


def load_test2_snapshots(n_files: int = 40,
                         bearing_id: int = 1,
                         source: str = "auto") -> pd.DataFrame:
    """
    Return a tidy DataFrame with one row per file:
        file_idx, t_norm, timestamp, samples (np.ndarray of 20480 floats)

    source:
        "real"      — require real IMS files (fails if not configured)
        "synthetic" — always synthesize
        "auto"      — use real if IMS_DATA_ROOT is set, else synthesize
    """
    if source == "real" or (source == "auto" and real_data_root() is not None):
        return _load_real(n_files, bearing_id)
    return _load_synthetic(n_files, bearing_id)


def _load_synthetic(n_files: int, bearing_id: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed=42 + bearing_id)
    ts_start = pd.Timestamp("2004-02-12 10:32:39")
    rows = []
    for i in range(n_files):
        t_norm = i / max(1, n_files - 1)
        samples = _synth_one_file(t_norm, rng)
        rows.append({
            "file_idx": i,
            "t_norm":   t_norm,
            "timestamp": ts_start + pd.Timedelta(minutes=10 * i),
            "samples":  samples,
            "source":   "synthetic",
        })
    return pd.DataFrame(rows)


def _load_real(n_files: int, bearing_id: int) -> pd.DataFrame:
    root = real_data_root()
    if root is None:
        raise FileNotFoundError(
            f"Real IMS dataset not found. Set {IMS_ROOT_ENV} to the folder "
            f"holding test-2 files (each file is a timestamp filename).")
    # each file has 4 columns (bearings 1..4)
    files = sorted(root.iterdir())
    # sub-sample evenly across the test
    idxs = np.linspace(0, len(files) - 1, n_files).astype(int)
    rows = []
    for i, fidx in enumerate(idxs):
        f = files[fidx]
        data = np.loadtxt(f)              # shape (20480, 4)
        col = bearing_id - 1              # 1-indexed → 0-indexed
        samples = data[:, col]
        ts = pd.to_datetime(f.name, format="%Y.%m.%d.%H.%M.%S")
        rows.append({
            "file_idx": i,
            "t_norm":   i / max(1, n_files - 1),
            "timestamp": ts,
            "samples":  samples,
            "source":   "real",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Feature extraction — the standard set for bearing prognostics
# ---------------------------------------------------------------------------
def extract_features(samples: np.ndarray, fs: int = SAMPLING_HZ) -> dict:
    """
    Classical time+frequency features that track bearing wear progression.
    All are standard in the CWRU / IMS / MFPT prognostics literature.
    """
    x = np.asarray(samples, dtype=float)
    N = len(x)
    if N == 0:
        return {k: np.nan for k in [
            "rms", "peak", "crest_factor", "kurtosis", "skewness",
            "peak_to_peak", "shape_factor", "impulse_factor",
            "spec_energy_2_5kHz", "spec_energy_high",
        ]}

    mean = x.mean()
    std  = x.std()
    rms  = np.sqrt(np.mean(x ** 2))
    peak = np.max(np.abs(x))
    p2p  = x.max() - x.min()
    mean_abs = np.mean(np.abs(x))

    kurt = ((x - mean) ** 4).mean() / (std ** 4 + 1e-12) - 3.0
    skew = ((x - mean) ** 3).mean() / (std ** 3 + 1e-12)
    crest = peak / (rms + 1e-12)
    shape = rms / (mean_abs + 1e-12)
    impulse = peak / (mean_abs + 1e-12)

    # simple periodogram — no windowing, this is fine for feature extraction
    freqs = np.fft.rfftfreq(N, d=1.0 / fs)
    X = np.abs(np.fft.rfft(x - mean)) / N
    # bearing fault energy typically lives around 2-5 kHz (impulse ringing)
    band1 = (freqs >= 2000) & (freqs <= 5000)
    band2 = (freqs >= 5000) & (freqs <= 10000)
    E_band1 = float(np.sum(X[band1] ** 2))
    E_band2 = float(np.sum(X[band2] ** 2))

    return {
        "rms": float(rms),
        "peak": float(peak),
        "crest_factor": float(crest),
        "kurtosis": float(kurt),
        "skewness": float(skew),
        "peak_to_peak": float(p2p),
        "shape_factor": float(shape),
        "impulse_factor": float(impulse),
        "spec_energy_2_5kHz": E_band1,
        "spec_energy_high":   E_band2,
    }


def feature_frame(snapshots_df: pd.DataFrame,
                  fs: int = SAMPLING_HZ) -> pd.DataFrame:
    rows = []
    for _, r in snapshots_df.iterrows():
        f = extract_features(r["samples"], fs)
        f["file_idx"] = r["file_idx"]
        f["t_norm"]   = r["t_norm"]
        f["timestamp"] = r["timestamp"]
        f["source"] = r["source"]
        rows.append(f)
    return pd.DataFrame(rows).sort_values("file_idx").reset_index(drop=True)
