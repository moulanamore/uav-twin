model UAVPropulsion "Digital-twin physics for a 4-motor electric UAV"

  // ---------- Battery parameters -----------------------------------------
  parameter Real Q_nom_Ah      = 5.0    "Nominal pack capacity (Ah)";
  parameter Real R_int_base    = 0.020  "Pack internal resistance at full SoC (ohm)";
  parameter Real R_int_slope   = 0.005  "Extra resistance at SoC=0 (ohm)";
  parameter Integer n_cells    = 4      "Cells in series";
  parameter Real V_cell_empty  = 3.30   "Per-cell voltage at SoC=0 (V)";
  parameter Real V_cell_range  = 0.90   "Per-cell voltage swing over SoC (V)";
  parameter Real SoH_wear_perAh = 1.0e-4 "SoH loss per Ah of throughput";

  // ---------- Motor parameters (shared across 4 motors) ------------------
  parameter Real R_motor       = 0.045  "Winding resistance (ohm)";
  parameter Real Kv_rpm_per_V  = 900    "Motor Kv (rpm/V no-load)";
  parameter Real C_th          = 40.0   "Thermal mass (J/K)";
  parameter Real h_conv        = 0.6    "Convective loss (W/K)";
  parameter Real T_ambient     = 30.0   "Ambient temperature (degC)";
  parameter Real P_idle_frac   = 0.05   "Idle iron/windage loss fraction (W per pack V)";

  // ---------- Bearing damage parameters ----------------------------------
  parameter Real bearing_rate_ref = 5.55e-7
      "Damage rate at ref conditions (per second, tuned to ~500 Ah -> 1.0)";
  parameter Real I_ref        = 8.0    "Reference current (A)";
  parameter Real I_exp        = 2.5    "Current exponent";
  parameter Real T_arrhenius_k = 0.06  "Temperature accelerator (per degC above ref)";
  parameter Real T_ref        = 45.0   "Reference temperature (degC)";

  // ---------- Inputs (fed from telemetry each step) ----------------------
  input Real i_pack "Instantaneous pack current, A (positive = discharge)";
  input Real i_m0   "Motor 0 current, A";
  input Real i_m1   "Motor 1 current, A";
  input Real i_m2   "Motor 2 current, A";
  input Real i_m3   "Motor 3 current, A";

  // ---------- State variables (also exposed as outputs so the worker
  // ---------- can read them each step) ---------------------------------
  output Real SoC(start = 1.0, fixed = true) "State of charge, 0..1";
  output Real SoH(start = 1.0, fixed = true) "State of health, 0..1";
  output Real Q_throughput(start = 0.0, fixed = true) "Cumulative Ah throughput";
  output Real T_m0(start = T_ambient, fixed = true) "Motor 0 temperature, degC";
  output Real T_m1(start = T_ambient, fixed = true) "Motor 1 temperature, degC";
  output Real T_m2(start = T_ambient, fixed = true) "Motor 2 temperature, degC";
  output Real T_m3(start = T_ambient, fixed = true) "Motor 3 temperature, degC";
  output Real D_m0(start = 0.0, fixed = true) "Motor 0 bearing damage, 0..1";
  output Real D_m1(start = 0.0, fixed = true) "Motor 1 bearing damage, 0..1";
  output Real D_m2(start = 0.0, fixed = true) "Motor 2 bearing damage, 0..1";
  output Real D_m3(start = 0.0, fixed = true) "Motor 3 bearing damage, 0..1";

  // ---------- Derived outputs (read by the worker each step) -------------
  output Real V_ocv          "Open-circuit pack voltage, V";
  output Real V_pack         "Terminal pack voltage, V";
  output Real R_int          "Current internal resistance, ohm";
  output Real P_loss_m0      "Motor 0 instantaneous power loss, W";
  output Real P_loss_m1      "Motor 1 instantaneous power loss, W";
  output Real P_loss_m2      "Motor 2 instantaneous power loss, W";
  output Real P_loss_m3      "Motor 3 instantaneous power loss, W";
  output Real rpm_m0         "Motor 0 no-load-equivalent RPM";
  output Real rpm_m1         "Motor 1 no-load-equivalent RPM";
  output Real rpm_m2         "Motor 2 no-load-equivalent RPM";
  output Real rpm_m3         "Motor 3 no-load-equivalent RPM";

equation
  // ------- Battery electrics ---------------------------------------------
  R_int = R_int_base + R_int_slope * (1.0 - SoC);
  V_ocv = n_cells * (V_cell_empty + V_cell_range * SoC ^ 0.9);
  V_pack = V_ocv - i_pack * R_int;

  // ------- Battery integrators -------------------------------------------
  der(SoC) = -i_pack / (Q_nom_Ah * SoH * 3600.0);
  der(Q_throughput) = abs(i_pack) / 3600.0;
  der(SoH) = -SoH_wear_perAh * abs(i_pack) / 3600.0;

  // ------- Motor thermal (single mass, per motor) ------------------------
  // Loss = copper + a small idle loss driven by pack voltage
  P_loss_m0 = i_m0 * i_m0 * R_motor + P_idle_frac * V_pack;
  P_loss_m1 = i_m1 * i_m1 * R_motor + P_idle_frac * V_pack;
  P_loss_m2 = i_m2 * i_m2 * R_motor + P_idle_frac * V_pack;
  P_loss_m3 = i_m3 * i_m3 * R_motor + P_idle_frac * V_pack;

  C_th * der(T_m0) = P_loss_m0 - h_conv * (T_m0 - T_ambient);
  C_th * der(T_m1) = P_loss_m1 - h_conv * (T_m1 - T_ambient);
  C_th * der(T_m2) = P_loss_m2 - h_conv * (T_m2 - T_ambient);
  C_th * der(T_m3) = P_loss_m3 - h_conv * (T_m3 - T_ambient);

  // ------- Motor RPM (back-emf balance, no-load equivalent) --------------
  rpm_m0 = max(0.0, (V_pack - i_m0 * R_motor)) * Kv_rpm_per_V;
  rpm_m1 = max(0.0, (V_pack - i_m1 * R_motor)) * Kv_rpm_per_V;
  rpm_m2 = max(0.0, (V_pack - i_m2 * R_motor)) * Kv_rpm_per_V;
  rpm_m3 = max(0.0, (V_pack - i_m3 * R_motor)) * Kv_rpm_per_V;

  // ------- Bearing damage — Paris-law-inspired accumulator ---------------
  der(D_m0) = bearing_rate_ref
              * (max(0.1, abs(i_m0)) / I_ref) ^ I_exp
              * exp(T_arrhenius_k * (T_m0 - T_ref));
  der(D_m1) = bearing_rate_ref
              * (max(0.1, abs(i_m1)) / I_ref) ^ I_exp
              * exp(T_arrhenius_k * (T_m1 - T_ref));
  der(D_m2) = bearing_rate_ref
              * (max(0.1, abs(i_m2)) / I_ref) ^ I_exp
              * exp(T_arrhenius_k * (T_m2 - T_ref));
  der(D_m3) = bearing_rate_ref
              * (max(0.1, abs(i_m3)) / I_ref) ^ I_exp
              * exp(T_arrhenius_k * (T_m3 - T_ref));

  annotation(
    experiment(StartTime = 0, StopTime = 300, Tolerance = 1e-6, Interval = 0.1),
    Documentation(info = "<html>
    <h4>UAV Propulsion Twin — Modelica model</h4>
    <p>Battery pack + 4 motors + bearing wear, ODE-based. All state
    integrators run at whatever step size the FMI master specifies (10 Hz in
    our worker). Same underlying physics as the Phase 1a Python model but
    Modelica's compiler generates a proper stiff-DAE solver and portable
    FMU wrapper.</p>
    <p>License: Apache-2.0</p>
    </html>")
  );
end UAVPropulsion;
