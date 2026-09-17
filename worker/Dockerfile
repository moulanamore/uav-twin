# -----------------------------------------------------------------------------
# Twin worker -- Phase 3c
# Two-stage build:
#   1. openmodelica image compiles the Modelica source to a Linux-x86_64 FMU
#   2. python:3.12-slim runs the FMU via fmpy, plus the Phase 3 ML models
# A pre-compiled FMU is committed to the repo so the build can also skip stage 1
# if OpenModelica isn't wanted. See ./models/UAVPropulsion.fmu.
# -----------------------------------------------------------------------------

# ----- Stage 1: FMU builder --------------------------------------------------
FROM openmodelica/openmodelica:v1.24.0-minimal AS fmu-builder
WORKDIR /build
COPY models/UAVPropulsion.mo models/build_fmu.mos ./
# omc emits <ModelName>.fmu in the working directory
RUN omc build_fmu.mos && ls -la UAVPropulsion.fmu

# ----- Stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app

# libgomp1 is needed by the FMU's compiled binary at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Python sources (add ml_runner for Phase 3c)
COPY twin_worker.py fmu_runner.py ml_runner.py ./

# Models: source Modelica + freshly-built FMU + trained ML pickles/configs
COPY models/UAVPropulsion.mo models/build_fmu.mos ./models/
COPY --from=fmu-builder /build/UAVPropulsion.fmu ./models/UAVPropulsion.fmu
COPY models/bearing_wear.pkl  models/bearing_wear.json  ./models/
COPY models/battery_rul.pkl   models/battery_rul.json   ./models/

CMD ["python", "-u", "twin_worker.py"]
