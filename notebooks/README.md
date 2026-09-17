# Phase 3 notebooks

Data exploration and model training for the twin's ML layer.

## Setup (one-time, 5 minutes)

```powershell
cd C:\Users\moula\claude_twin\uav-twin\notebooks

# Create a Python virtualenv so we don't pollute the system Python
python -m venv .venv
.venv\Scripts\activate            # PowerShell
# or: .venv\bin\activate on macOS/Linux

pip install -r requirements.txt
jupyter lab
```

Then open `01_ims_bearing_exploration.ipynb` in the JupyterLab window that
opens in your browser and run every cell (Kernel menu → Restart & Run All).

## What each notebook does

| Notebook | What it explores | What it produces |
|---|---|---|
| `01_ims_bearing_exploration.ipynb` | NASA IMS Bearing run-to-failure — the shape of the wear signal we're training against | `data/ims_features/*.parquet` |
| `02_alfa_fault_flights.ipynb` *(next)* | ALFA UAV fault-injection dataset — real motor telemetry with labels | `data/alfa_features/*.parquet` |
| `03_nasa_battery_aging.ipynb` *(next)* | NASA Li-ion aging — Model B ground truth | `data/battery_features/*.parquet` |
| `04_model_a_xgboost.ipynb` *(next)* | Train the bearing wear classifier | `worker/models/bearing_wear.pkl` |

## Real vs synthetic data

Every notebook has a **synthetic fallback** so you can run it today without
downloading anything. When you want the real thing:

**NASA IMS Bearing** (5 GB):
1. Download from <https://www.kaggle.com/datasets/vinayak123tyagi/bearing-dataset>
   (or the NASA PCoE mirror if it's back up).
2. Unzip. Point at the `2nd_test/` folder — every file inside is a timestamp filename with 4 columns.
3. In the first cell of `01_ims_bearing_exploration.ipynb`:
   ```python
   import os
   os.environ['IMS_DATA_ROOT'] = r'C:\Users\moula\Downloads\ims\2nd_test'
   ```
4. Run all cells. Nothing else changes.

**ALFA UAV faults** (2 GB) — same pattern; instructions in notebook 02.

**NASA Li-ion battery aging** (500 MB) — same pattern; instructions in notebook 03.

## Repo layout

```
notebooks/
├── README.md              (this file)
├── requirements.txt       (jupyterlab, numpy, pandas, scipy, matplotlib, xgboost, pyarrow)
├── utils/
│   ├── nasa_ims_loader.py     # dataset loader + feature extraction
│   └── (more helpers as we go)
├── data/                  (Parquet feature files — .gitignored, regenerable)
└── 01_ims_bearing_exploration.ipynb
```

## When you're ready to train

`04_model_a_xgboost.ipynb` will produce a `bearing_wear.pkl` that drops into
`worker/models/` alongside the FMU. The twin worker's `fmu_runner.py` will
grow a sibling `ml_runner.py` that loads the pickle and steps it each
tick alongside the FMU. Same fallback pattern: if the pickle is missing,
worker falls back to Phase 1c's analytical predictor.
