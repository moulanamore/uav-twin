"""
NASA Li-ion battery aging loader — synthetic fallback + real-data path.

Original data: NASA Prognostics Center of Excellence, Li-ion battery aging
    Reference: Saha & Goebel, "Battery data set", NASA Ames PCoE, 2007.
    Canonical mirror: https://data.nasa.gov/dataset/li-ion-battery-aging-datasets
    Format: MATLAB .mat files. Each battery (B0005/B0006/B0007/B0018)
    was cycled to failure (~30% capacity fade) at 24 °C ambient.

Each cycle carries:
    - charge (constant current 1.5A → constant voltage until 20 mA)
    - discharge (constant 2A until cutoff)
    - impedance measurement between cycles

We fit cycle-level features (per-cycle capacity, charge/discharge time,
internal resistance, peak temperature) — the same feature set our Model B
Gaussian process will train on.

For UAV application, we translate cycles → flights via the empirical
mapping: 1 flight ≈ 0.2 equivalent full cycle (partial discharge). This
lets us report RUL in "flights remaining" as the twin's dashboard needs.

License: Apache-2.0
"""

from __future__ import annotations

import os
import pathlib
import numpy as np
import pandas as pd


NASA_BATT_ROOT_ENV = "NASA_BATT_DATA_ROOT"

# Canonical battery meta from the NASA dataset
CANONICAL_BATTERIES = {
    "B0005": {"aging_rate": 1.00, "capacity_start": 1.856},
    "B0006": {"aging_rate": 1.35, "capacity_start": 2.035},   # ages faster
    "B0007": {"aging_rate": 0.85, "capacity_start": 1.891},
    "B0018": {"aging_rate": 1.15, "capacity_start": 1.855},
}

CAPACITY_NOMINAL = 2.0        # Ah — 18650 nominal
EOL_SOH          = 0.70       # NASA-standard EoL threshold (~30% fade)
CYCLES_PER_FLIGHT = 0.2       # each flight ≈ 20% of nominal capacity


def real_data_root() -> pathlib.Path | None:
    root = os.environ.get(NASA_BATT_ROOT_ENV)
    if root and pathlib.Path(root).exists():
        return pathlib.Path(root)
    return None


# ---------------------------------------------------------------------------
# Synthetic cell aging trajectory
# ---------------------------------------------------------------------------
def _synth_one_battery(battery_id: str, n_cycles: int,
                       rng: np.random.Generator) -> pd.DataFrame:
    """
    Generate a plausible cycle-by-cycle aging trajectory for one cell.
    Uses a two-phase capacity-fade model:
      * Phase 1: linear moderate fade (calendar + cycling wear)
      * Phase 2: accelerating knee near EoL (SEI-driven, well-documented)
    Plus correlated internal-resistance rise and temperature drift.
    """
    meta = CANONICAL_BATTERIES[battery_id]
    aging_k = meta["aging_rate"]
    C0 = meta["capacity_start"]

    # Cycle-normalised aging
    c = np.arange(n_cycles)
    # Phase 1: gentle linear fade
    linear = 0.0012 * aging_k * c
    # Phase 2: knee kicks in around cycle 100-140 depending on aging_rate
    knee_cycle = 120.0 / aging_k
    x = np.maximum(0, c - knee_cycle) / (n_cycles - knee_cycle + 1e-6)
    knee = 0.35 * (x ** 2.4)
    fade = np.clip(linear + knee, 0, 0.6)          # cap at 60% loss
    capacity = C0 * (1.0 - fade)

    # Internal resistance rises inversely with capacity (Rint · Q ~ constant
    # for a first-order model), plus a bit of noise
    R0 = 0.045
    Rint = R0 * (C0 / capacity) ** 1.4
    Rint += 0.002 * rng.standard_normal(n_cycles)

    # Charge time grows as R and internal losses grow
    charge_time_min  = 90.0 + 40.0 * (fade + rng.standard_normal(n_cycles) * 0.02)
    # Discharge time shrinks with capacity (fixed current)
    discharge_time_min = 60.0 * (capacity / C0) + rng.standard_normal(n_cycles) * 0.5

    # Peak cycle temperature — rises with resistance losses
    peak_temp_c = 26.0 + 8.0 * (Rint / R0) + rng.standard_normal(n_cycles) * 0.4

    # SoH
    soh = capacity / C0
    # RUL: flights remaining until SoH = EoL_SOH
    # Below EoL: 0. Above: linear extrapolation from current fade rate.
    rul_flights = np.full(n_cycles, -1.0)
    for i in range(1, n_cycles):
        if soh[i] <= EOL_SOH:
            rul_flights[i] = 0
        else:
            local_rate = (soh[i - 1] - soh[i]) / max(c[i] - c[i - 1], 1)
            if local_rate > 1e-6:
                cycles_to_eol = (soh[i] - EOL_SOH) / local_rate
                rul_flights[i] = cycles_to_eol / CYCLES_PER_FLIGHT
    rul_flights[0] = rul_flights[1]  # first cycle inherits

    return pd.DataFrame({
        "battery_id":         battery_id,
        "cycle":              c,
        "capacity_ah":        capacity,
        "soh":                soh,
        "internal_r_ohm":     Rint,
        "charge_time_min":    charge_time_min,
        "discharge_time_min": discharge_time_min,
        "peak_temp_c":        peak_temp_c,
        "rul_flights":        rul_flights,
        "source":             "synthetic",
    })


def load_batteries(battery_ids: list[str] | None = None,
                   n_cycles: int = 170,
                   source: str = "auto",
                   seed: int = 42) -> pd.DataFrame:
    """Return a long-format DataFrame with one row per (battery, cycle)."""
    battery_ids = battery_ids or list(CANONICAL_BATTERIES.keys())
    if source == "real" or (source == "auto" and real_data_root() is not None):
        return _load_real(battery_ids)
    rng = np.random.default_rng(seed)
    dfs = [_synth_one_battery(bid, n_cycles, rng) for bid in battery_ids]
    return pd.concat(dfs, ignore_index=True)


def _load_real(battery_ids: list[str]) -> pd.DataFrame:
    """
    Load pre-parsed NASA battery CSVs. Assumes user has extracted cycle-
    summary rows from the .mat files (see notebook 03 README).
    """
    root = real_data_root()
    dfs = []
    for bid in battery_ids:
        candidates = list(root.glob(f"*{bid}*.csv"))
        if not candidates:
            continue
        df = pd.read_csv(candidates[0])
        df["battery_id"] = bid
        df["source"] = "real"
        dfs.append(df)
    if not dfs:
        raise FileNotFoundError(
            f"No CSV files found for {battery_ids} under {root}. "
            f"See notebook 03 for the parsing script.")
    return pd.concat(dfs, ignore_index=True)


# ---------------------------------------------------------------------------
# Feature engineering for Model B
# ---------------------------------------------------------------------------
def add_rolling_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Per-battery rolling features that capture *trajectory* not just state:
      * capacity_slope       — rate of SoH decay over last `window` cycles
      * r_slope              — rate of resistance rise
      * temp_slope           — thermal drift
      * cycles_since_start   — trivial but useful as a covariate
    These are the covariates Model B's Gaussian process consumes on top of
    the physics twin's Coulomb-counted SoH state.
    """
    out = []
    for bid, group in df.groupby("battery_id", sort=False):
        g = group.sort_values("cycle").reset_index(drop=True).copy()
        g["capacity_slope"] = -g["soh"].diff(periods=window) / window
        g["r_slope"]        = g["internal_r_ohm"].diff(periods=window) / window
        g["temp_slope"]     = g["peak_temp_c"].diff(periods=window) / window
        g["cycles_elapsed"] = g["cycle"]
        # Discharge-time efficiency: how much of nominal we still deliver
        g["discharge_eff"]  = g["discharge_time_min"] / g["discharge_time_min"].iloc[:5].mean()
        # Fill NaNs from the diff() operation with zero (start-of-life)
        for c in ["capacity_slope", "r_slope", "temp_slope"]:
            g[c] = g[c].fillna(0.0)
        out.append(g)
    return pd.concat(out, ignore_index=True)
