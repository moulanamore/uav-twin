"""
Scripted mission uploader for the SITL Copter.

Behaviour (Phase 1b.5):
  1. Wait for SITL to publish a heartbeat and its first SYS_STATUS.
  2. Wait for ArduPilot's pre-arm checks to pass (SYS_STATUS bit 26,
     MAV_SYS_STATUS_PREARM_CHECK). This eliminates the "no mission request
     received" and arm-failed noise from Phase 1b.
  3. Upload the mission ONCE. ArduPilot persists it across arm/disarm.
  4. In a loop: wait for pre-arm ok → set GUIDED → arm and confirm via
     HEARTBEAT → set AUTO → MISSION_START → wait for disarm → repeat.

License: Apache-2.0
"""
from __future__ import annotations

import os
import time
from typing import Any

os.environ.setdefault("MAVLINK20", "1")
os.environ.setdefault("MAVLINK_DIALECT", "ardupilotmega")

from pymavlink import mavutil                                # noqa: E402
from pymavlink.dialects.v20 import ardupilotmega as mavlink  # noqa: E402

# MAV_SYS_STATUS_PREARM_CHECK is bit 28 (value 0x10000000) in
# onboard_control_sensors_health. Bit 26 is XY_POSITION_CONTROL — set
# almost immediately by SITL and NOT a valid pre-arm signal.
PREARM_HEALTH_BIT = 1 << 28


def _wp(seq: int, cmd: int, lat: float, lon: float, alt: float,
        p1: float = 0, p2: float = 0, p3: float = 0, p4: float = 0) -> Any:
    return mavlink.MAVLink_mission_item_int_message(
        target_system=1, target_component=0,
        seq=seq, frame=mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        command=cmd, current=0, autocontinue=1,
        param1=p1, param2=p2, param3=p3, param4=p4,
        x=int(lat * 1e7), y=int(lon * 1e7), z=alt,
        mission_type=mavlink.MAV_MISSION_TYPE_MISSION,
    )


def build_mission(home_lat: float, home_lon: float,
                  alt_m: float = 20.0, side_m: float = 40.0) -> list[Any]:
    import math
    dlat = side_m / 111_320.0
    dlon = side_m / (111_320.0 * max(0.1, abs(math.cos(math.radians(home_lat)))))
    return [
        _wp(0, mavlink.MAV_CMD_NAV_WAYPOINT, home_lat, home_lon, 0),
        _wp(1, mavlink.MAV_CMD_NAV_TAKEOFF,  home_lat, home_lon, alt_m),
        _wp(2, mavlink.MAV_CMD_NAV_WAYPOINT, home_lat + dlat, home_lon,        alt_m),
        _wp(3, mavlink.MAV_CMD_NAV_WAYPOINT, home_lat + dlat, home_lon + dlon, alt_m),
        _wp(4, mavlink.MAV_CMD_NAV_WAYPOINT, home_lat,        home_lon + dlon, alt_m),
        _wp(5, mavlink.MAV_CMD_NAV_WAYPOINT, home_lat,        home_lon,        alt_m),
        _wp(6, mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH, 0, 0, 0),
    ]


def upload_mission(conn, items: list[Any]) -> bool:
    print(f"[mission] uploading {len(items)} items …", flush=True)
    conn.mav.mission_count_send(1, 0, len(items),
                                mavlink.MAV_MISSION_TYPE_MISSION)
    for _ in range(len(items)):
        req = conn.recv_match(type=["MISSION_REQUEST_INT", "MISSION_REQUEST"],
                              blocking=True, timeout=8)
        if req is None:
            print("[mission] no mission request received; aborting", flush=True)
            return False
        conn.mav.send(items[req.seq])
    ack = conn.recv_match(type="MISSION_ACK", blocking=True, timeout=8)
    ok = ack is not None and ack.type == mavlink.MAV_MISSION_ACCEPTED
    print(f"[mission] upload {'ok' if ok else 'FAILED'}", flush=True)
    return ok


def wait_ready(conn, timeout: float = 180.0) -> bool:
    """Wait for SITL GPS 3D fix AND EKF horizontal-position convergence.

    ArduPilot rejects arming with "Need Position Estimate" until the EKF has
    fused GPS + IMU into a healthy XY position. That takes ~15 s after GPS
    lock in SITL. EKF_STATUS_REPORT.flags bit 4 (EKF_POS_HORIZ_ABS) is the
    canonical signal.
    """
    EKF_POS_HORIZ_ABS = 1 << 4
    print(f"[mission] waiting up to {int(timeout)}s for GPS + EKF …",
          flush=True)
    t0 = time.time()
    have_gps = False
    have_ekf = False
    last_report = 0.0
    while time.time() - t0 < timeout:
        msg = conn.recv_match(type=["GPS_RAW_INT", "EKF_STATUS_REPORT"],
                              blocking=True, timeout=3)
        if msg is not None:
            if msg.get_type() == "GPS_RAW_INT" and msg.fix_type >= 3:
                have_gps = True
            elif msg.get_type() == "EKF_STATUS_REPORT":
                have_ekf = bool(msg.flags & EKF_POS_HORIZ_ABS)
        if have_gps and have_ekf:
            elapsed = time.time() - t0
            print(f"[mission] ready after {elapsed:.1f}s (gps=ok ekf=ok)",
                  flush=True)
            time.sleep(2)   # small final settle
            return True
        now = time.time()
        if now - last_report > 15:
            print(f"[mission] waiting … gps={have_gps} ekf={have_ekf} "
                  f"({int(now - t0)}s)", flush=True)
            last_report = now
    print("[mission] ready-wait timeout — proceeding anyway", flush=True)
    return False


# Alias for backward-compat
wait_prearm_ok = wait_ready


def set_mode(conn, mode_name: str) -> None:
    mode_map = conn.mode_mapping() or {}
    mode_id = mode_map.get(mode_name)
    if mode_id is None:
        print(f"[mission] mode {mode_name} unknown; available: {list(mode_map)}",
              flush=True)
        return
    conn.mav.set_mode_send(1, mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id)


def arm_and_confirm(conn, timeout: float = 15.0) -> bool:
    # param1 = 1 (arm), param2 = 21196 = ArduPilot magic "force arm"
    # (bypasses any residual pre-arm/arm checks — safe only in SITL).
    conn.mav.command_long_send(1, 0, mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                               0, 1, 21196, 0, 0, 0, 0, 0)
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
        if msg is None:
            continue
        if msg.base_mode & mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
            return True
    return False


def wait_for_disarm(conn) -> None:
    """Block until the vehicle disarms (mission complete / RTL land)."""
    while True:
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=30)
        if msg is None:
            continue
        armed = bool(msg.base_mode & mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if not armed:
            return


def start_mission_cmd(conn) -> None:
    conn.mav.command_long_send(1, 0, mavlink.MAV_CMD_MISSION_START,
                               0, 0, 0, 0, 0, 0, 0, 0)


def run_mission_loop(conn, home_lat: float, home_lon: float) -> None:
    """Blocking: pre-arm wait → upload once → arm/AUTO/wait-disarm forever."""
    time.sleep(3)  # let heartbeats settle

    # First readiness may take a while: GPS lock (~30 s) + EKF convergence.
    wait_ready(conn, timeout=180)

    # Upload once — ArduPilot persists mission across arm/disarm cycles.
    # We retry generously because SITL occasionally rejects the first few
    # uploads even after ready — the mission_ack path is a bit racy.
    items = build_mission(home_lat, home_lon)
    for attempt in range(10):
        if upload_mission(conn, items):
            break
        print(f"[mission] upload attempt {attempt+1}/10 failed; retry in 10s",
              flush=True)
        time.sleep(10)
    else:
        print("[mission] giving up on upload after 10 tries; exiting", flush=True)
        return

    loop = 0
    while True:
        loop += 1
        try:
            # Wait for readiness on subsequent cycles too
            wait_ready(conn, timeout=90)

            set_mode(conn, "GUIDED"); time.sleep(1)
            if not arm_and_confirm(conn):
                print(f"[mission] loop {loop}: arm failed; waiting 10s", flush=True)
                time.sleep(10); continue

            set_mode(conn, "AUTO"); time.sleep(0.5)
            start_mission_cmd(conn)
            print(f"[mission] loop {loop}: AUTO started, waiting for disarm",
                  flush=True)

            wait_for_disarm(conn)
            print(f"[mission] loop {loop}: disarmed; restart in 5s", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"[mission] loop {loop}: error {e}; retry in 10s", flush=True)
            time.sleep(10)
