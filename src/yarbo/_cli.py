"""
yarbo._cli — CLI entry point for the yarbo package.

Exposes discovery and full local robot control (lights, buzzer, plans, schedules,
manual drive, etc.). Use --broker and --sn if the robot is known; otherwise
the CLI discovers and uses primary/fallback order.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from yarbo.discovery import connection_order, discover
from yarbo.exceptions import YarboError
from yarbo.local import YarboLocalClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_CLI_EPILOG = """
Commands (grouped)
──────────────────

Discovery
  discover      Find Yarbo MQTT brokers (Rover/DC) on the network.
  status        Connect (primary/fallback) and print robot status.

Status & telemetry
  status        Robot status (battery, state, heading).
  battery       Print battery percentage only.
  telemetry     Stream live telemetry (Ctrl+C to stop).

Control
  lights-on     Turn all lights on.
  lights-off    Turn all lights off.
  buzzer        Start buzzer (--stop to stop).
  chute         Set chute direction (snow blower): --vel N.
  return-to-dock  Send robot to charging dock.

Plans
  plans         List saved plans.
  plan-start    Start plan by ID: --plan-id ID.
  plan-stop     Stop current plan.
  plan-pause    Pause current plan.
  plan-resume   Resume paused plan.

Schedules
  schedules     List saved schedules.

Manual drive
  manual-start  Enter manual drive mode.
  velocity      Set velocity: --linear N [--angular N].
  roller        Set roller speed: --speed N.
  manual-stop   Stop manual drive (--mode emergency|normal|idle).

Other
  global-params Get global parameters (JSON).
  map           Get map data (JSON to stdout or --out FILE).

Connection (optional; omit to auto-discover)
  --broker IP   Broker IP (default: discover).
  --sn SERIAL   Robot serial (default: from discover).
  --port PORT   MQTT port (default: 1883).
  --subnet CIDR Subnet to scan when discovering.
  --timeout N   Timeout in seconds (default: 5).
  --max-hosts N Max hosts per subnet (default: 512).

Test (with robot on network; omit --broker/--sn to auto-discover)
  yarbo discover
  yarbo status
  yarbo battery
  yarbo lights-on
  yarbo lights-off
  yarbo buzzer
  yarbo buzzer --stop
  yarbo return-to-dock
  yarbo plans
  yarbo schedules
  yarbo global-params
  yarbo map --out map.json
  yarbo plan-start --plan-id <ID>
  yarbo plan-stop
  yarbo manual-start
  yarbo velocity --linear 0.5
  yarbo manual-stop
"""

#: Key substrings that indicate a field may contain credentials; values are redacted.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "access_key",
        "credential",
        "auth_key",
        "auth",
    }
)


def _is_sensitive_key(k: str) -> bool:
    """Return True if the dotted key name suggests it contains credentials."""
    k_lower = k.lower()
    return any(s in k_lower for s in _SENSITIVE_KEYS)


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--broker",
        type=str,
        default=None,
        help="Broker IP (omit to auto-discover).",
    )
    parser.add_argument(
        "--sn",
        type=str,
        default=None,
        dest="serial",
        help="Robot serial number (omit to auto-discover).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="MQTT port (default: 1883).",
    )
    parser.add_argument(
        "--subnet",
        type=str,
        default=None,
        help="Subnet to scan when auto-discovering (e.g. 192.0.2.0/24).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout in seconds (default: 5).",
    )
    parser.add_argument(
        "--max-hosts",
        type=int,
        default=512,
        metavar="N",
        help="Max hosts per subnet when auto-discovering (default: 512).",
    )
    parser.add_argument(
        "--log-mqtt",
        type=str,
        default=None,
        metavar="FILE",
        dest="log_mqtt",
        help="Append every raw MQTT message (topic + payload JSON) to FILE.",
    )


async def _with_client(
    args: argparse.Namespace,
) -> AsyncIterator[tuple[YarboLocalClient, str | None]]:
    """Yield (connected client, endpoint_ip_or_none). Disconnects on exit."""
    if getattr(args, "broker", None) and getattr(args, "serial", None):
        client = YarboLocalClient(
            broker=args.broker,
            sn=args.serial,
            port=getattr(args, "port", 1883),
            mqtt_log_path=getattr(args, "log_mqtt", None),
        )
        await client.connect()
        try:
            yield (client, args.broker)
        finally:
            await client.disconnect()
        return

    endpoints = await discover(
        timeout=getattr(args, "timeout", 5.0),
        port=getattr(args, "port", 1883),
        subnet=getattr(args, "subnet", None),
        max_hosts=getattr(args, "max_hosts", 512),
    )
    if not endpoints:
        raise SystemExit(
            "No Yarbo endpoints found. Use --broker and --sn or run on a network with a robot."
        )
    ordered = connection_order(endpoints)
    last_err: Exception | None = None
    for ep in ordered:
        try:
            client = YarboLocalClient(
                broker=ep.ip,
                port=ep.port,
                sn=ep.sn,
                mqtt_log_path=getattr(args, "log_mqtt", None),
            )
            await client.connect()
        except (YarboError, OSError, TimeoutError) as e:
            last_err = e
            continue
        try:
            yield (client, ep.ip)
            return
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
    raise SystemExit(f"All endpoints failed. Last error: {last_err}")


def _main() -> None:  # noqa: PLR0915
    parser = argparse.ArgumentParser(
        prog="yarbo",
        description="Yarbo robot local and cloud control (MQTT).",
        epilog=_CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Discovery, status, control, plans, schedules, manual drive.",
    )

    # ----- Discovery -----
    discover_parser = subparsers.add_parser(
        "discover", help="Find Yarbo brokers (Rover/DC) on the network."
    )
    discover_parser.add_argument(
        "--subnet", type=str, default=None, help="Subnet to scan (e.g. 192.0.2.0/24)."
    )
    discover_parser.add_argument(
        "--timeout", type=float, default=5.0, help="Timeout per probe (default: 5)."
    )
    discover_parser.add_argument(
        "--port", type=int, default=1883, help="MQTT port (default: 1883)."
    )
    discover_parser.add_argument(
        "--max-hosts",
        type=int,
        default=512,
        metavar="N",
        help="Max hosts per subnet (default: 512).",
    )

    status_parser = subparsers.add_parser(
        "status", help="Connect (primary/fallback) and print robot status."
    )
    _add_connection_args(status_parser)

    # ----- Status & telemetry -----
    battery_parser = subparsers.add_parser("battery", help="Print battery percentage.")
    _add_connection_args(battery_parser)

    telemetry_parser = subparsers.add_parser(
        "telemetry", help="Stream live telemetry (Ctrl+C to stop)."
    )
    _add_connection_args(telemetry_parser)

    # ----- Control -----
    lights_on_parser = subparsers.add_parser("lights-on", help="Turn all lights on.")
    _add_connection_args(lights_on_parser)

    lights_off_parser = subparsers.add_parser("lights-off", help="Turn all lights off.")
    _add_connection_args(lights_off_parser)

    buzzer_parser = subparsers.add_parser("buzzer", help="Start buzzer (--stop to stop).")
    _add_connection_args(buzzer_parser)
    buzzer_parser.add_argument("--stop", action="store_true", help="Stop buzzer.")

    chute_parser = subparsers.add_parser(
        "chute", help="Set chute direction (snow blower): --vel N."
    )
    _add_connection_args(chute_parser)
    chute_parser.add_argument(
        "--vel",
        type=int,
        required=True,
        help="Chute velocity (positive=right, negative=left).",
    )

    return_parser = subparsers.add_parser("return-to-dock", help="Send robot to charging dock.")
    _add_connection_args(return_parser)

    # ----- Plans -----
    plans_parser = subparsers.add_parser("plans", help="List saved plans.")
    _add_connection_args(plans_parser)

    plan_start_parser = subparsers.add_parser("plan-start", help="Start plan by ID.")
    _add_connection_args(plan_start_parser)
    plan_start_parser.add_argument("--plan-id", type=str, required=True, help="Plan ID to start.")

    plan_stop_parser = subparsers.add_parser("plan-stop", help="Stop current plan.")
    _add_connection_args(plan_stop_parser)

    plan_pause_parser = subparsers.add_parser("plan-pause", help="Pause current plan.")
    _add_connection_args(plan_pause_parser)

    plan_resume_parser = subparsers.add_parser("plan-resume", help="Resume paused plan.")
    _add_connection_args(plan_resume_parser)

    # ----- Schedules -----
    schedules_parser = subparsers.add_parser("schedules", help="List saved schedules.")
    _add_connection_args(schedules_parser)

    # ----- Manual drive -----
    manual_start_parser = subparsers.add_parser("manual-start", help="Enter manual drive mode.")
    _add_connection_args(manual_start_parser)

    velocity_parser = subparsers.add_parser("velocity", help="Set velocity (manual mode).")
    _add_connection_args(velocity_parser)
    velocity_parser.add_argument("--linear", type=float, required=True, help="Linear velocity.")
    velocity_parser.add_argument(
        "--angular", type=float, default=0.0, help="Angular velocity (default: 0)."
    )

    roller_parser = subparsers.add_parser("roller", help="Set roller speed.")
    _add_connection_args(roller_parser)
    roller_parser.add_argument("--speed", type=int, required=True, help="Roller speed.")

    manual_stop_parser = subparsers.add_parser("manual-stop", help="Stop manual drive.")
    _add_connection_args(manual_stop_parser)
    manual_stop_parser.add_argument(
        "--mode",
        type=str,
        choices=("emergency", "normal", "idle"),
        default="normal",
        help="Stop mode (default: normal).",
    )

    # ----- Other -----
    global_params_parser = subparsers.add_parser(
        "global-params", help="Get global parameters (JSON)."
    )
    _add_connection_args(global_params_parser)

    map_parser = subparsers.add_parser("map", help="Get map data (JSON).")
    _add_connection_args(map_parser)
    map_parser.add_argument(
        "--out", type=str, default=None, help="Write to file (default: stdout)."
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "discover": _run_discover,
        "status": _run_status,
        "battery": _run_battery,
        "telemetry": _run_telemetry,
        "lights-on": _run_lights_on,
        "lights-off": _run_lights_off,
        "buzzer": _run_buzzer,
        "chute": _run_chute,
        "return-to-dock": _run_return_to_dock,
        "plans": _run_plans,
        "plan-start": _run_plan_start,
        "plan-stop": _run_plan_stop,
        "plan-pause": _run_plan_pause,
        "plan-resume": _run_plan_resume,
        "schedules": _run_schedules,
        "manual-start": _run_manual_start,
        "velocity": _run_velocity,
        "roller": _run_roller,
        "manual-stop": _run_manual_stop,
        "global-params": _run_global_params,
        "map": _run_map,
    }
    handler = handlers.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


# ----- Discovery (no client) -----
async def _run_discover(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if args.subnet:
        print(f"Scanning {args.subnet} for Yarbo brokers...")
    else:
        print("Scanning local network(s) for Yarbo brokers...")
    endpoints = await discover(
        timeout=args.timeout,
        port=args.port,
        subnet=args.subnet,
        max_hosts=args.max_hosts,
    )
    if not endpoints:
        print("No Yarbo endpoints found.")
        sys.exit(1)
    col_ip = max(len(e.ip) for e in endpoints) + 1
    col_port, col_path, col_mac = 6, 6, max(len(e.mac or "(no MAC)") for e in endpoints) + 1
    col_rec, col_sn = 2, max(len(e.sn or "-") for e in endpoints) + 1
    fmt = (
        f"{{:<{col_ip}}} {{:<{col_port}}} {{:<{col_path}}} "
        f"{{:<{col_mac}}} {{:<{col_rec}}} {{:<{col_sn}}}{{}}"
    )
    print(fmt.format("IP", "PORT", "PATH", "MAC", "", "SN", ""))
    print("-" * (col_ip + col_port + col_path + col_mac + col_rec + col_sn + 20))
    for e in endpoints:
        rec = "*" if e.recommended else ""
        hostname = f" ({e.hostname})" if e.hostname else ""
        print(
            fmt.format(
                e.ip, str(e.port), e.path.upper(), e.mac or "(no MAC)", rec, e.sn or "-", hostname
            )
        )
    if any(e.recommended for e in endpoints):
        print("\n* = recommended (prefer this endpoint when robot may be out of WiFi range)")


# ----- Status (with fallback) -----
async def _run_status(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if not (args.broker and args.serial):
        print("Discovering...")
    async for client, ip in _with_client(args):
        status = await asyncio.wait_for(client.get_status(), timeout=args.timeout)
        if status:
            sn = args.serial if args.broker and args.serial else client.serial_number
            _print_status(status, ip or args.broker, sn)
        else:
            print("Error: connected but no telemetry received within timeout.")
            sys.exit(1)
        break


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if v is True:
        return "True"
    if v is False:
        return "False"
    return str(v)


def _print_status(status: Any, ip: str, sn: str) -> None:
    """Print full telemetry (PowerShell-style) so we expose all available data."""
    # Order and labels aligned with PowerShell / app for consistency
    fields = [
        ("SerialNumber", status.sn or sn),
        ("BrokerIP", ip),
        ("Name", status.name),
        ("HeadType", status.head_type),
        ("BatteryCapacity", status.battery),
        ("WorkingState", status.working_state),
        ("ChargingStatus", status.charging_status),
        ("ErrorCode", status.error_code),
        ("RtkStatus", status.rtk_status),
        ("Heading", status.heading),
        (
            "LastUpdated",
            datetime.fromtimestamp(status.last_updated, tz=UTC).isoformat()
            if status.last_updated is not None
            else None,
        ),
        ("HeadSerialNumber", status.head_serial_number),
        ("BatteryStatus", status.battery_status),
        ("BatteryTempErr", status.battery_temp_err),
        ("IsPlanning", status.on_going_planning),
        ("IsPaused", status.planning_paused),
        ("IsRecharging", status.on_going_recharging),
        ("CarController", status.car_controller),
        ("MachineController", status.machine_controller),
        ("OdometryX", status.position_x),
        ("OdometryY", status.position_y),
        ("OdometryPhi", status.phi),
        ("OdomConfidence", status.odom_confidence),
        ("ChuteAngle", status.chute_angle),
        ("LedRegister", str(status.led) if status.led is not None else None),
        ("WirelessChargeVoltage", status.wireless_charge_voltage),
        ("WirelessChargeCurrent", status.wireless_charge_current),
        ("WirelessRechargeState", status.wireless_recharge_state),
        ("WirelessRechargeErrorCode", status.wireless_recharge_error_code),
        ("RoutePriority", status.route_priority),
        ("State", status.state),
        ("Speed", status.speed),
        # Latitude/Longitude are intentionally exposed (core feature for location queries)
        ("Latitude", status.latitude),
        ("Longitude", status.longitude),
        ("Altitude", status.altitude),
        ("FixQuality", status.fix_quality),
        # Extended MQTT fields
        ("BodyRechargeState", status.body_recharge_state),
        ("RtkGgaAtnDis", status.rtk_gga_atn_dis),
        ("RtkHeadingAtnDis", status.rtk_heading_atn_dis),
        ("RtkHeadingDop", status.rtk_heading_dop),
        ("RtkHeadingStatus", status.rtk_heading_status),
        ("RtkPre4Timestamp", status.rtk_pre4_timestamp),
        ("RtkVersion", status.rtk_version),
        ("ChuteSteeringEngineInfo", status.chute_steering_engine_info),
        ("ElecNavFrontRightSensor", status.elec_navigation_front_right_sensor),
        ("ElecNavRearRightSensor", status.elec_navigation_rear_right_sensor),
        ("HeadGyroPitch", status.head_gyro_pitch),
        ("HeadGyroRoll", status.head_gyro_roll),
        ("RainSensorData", status.rain_sensor_data),
        ("AdjustangleStatus", status.adjustangle_status),
        ("AutoDrawWaitingState", status.auto_draw_waiting_state),
        ("EnStateLed", status.en_state_led),
        ("EnWarnLed", status.en_warn_led),
        ("OnGoingToStartPoint", status.on_going_to_start_point),
        ("OnMulPoints", status.on_mul_points),
        ("RobotFollowState", status.robot_follow_state),
        ("ScheduleCancel", status.schedule_cancel),
        ("VisionAutoDrawState", status.vision_auto_draw_state),
        ("BaseStatus", status.base_status),
        ("Bds", status.bds),
        ("Bs", status.bs),
        ("Ms", status.ms),
        ("S", status.s),
        ("Sbs", status.sbs),
        ("Tms", status.tms),
        ("GreenGrassUpdateSwitch", status.green_grass_update_switch),
        ("IpcameraOtaSwitch", status.ipcamera_ota_switch),
        ("RtcmAge", status.rtcm_age),
        ("RtcmCurrentSourceType", status.rtcm_current_source_type),
        ("RtkBaseGngga", status.rtk_base_gngga),
        ("RtkRoverHeading", status.rtk_rover_heading),
        ("UltrasonicLfDis", status.ultrasonic_lf_dis),
        ("UltrasonicMtDis", status.ultrasonic_mt_dis),
        ("UltrasonicRfDis", status.ultrasonic_rf_dis),
        ("PushPodCurrent", status.push_pod_current),
    ]
    for label, value in fields:
        print(f"  {label:<24}: {_fmt(value)}")

    # Every value from the MQTT payload (flattened dotted keys); sensitive fields redacted.
    all_mqtt = status.all_mqtt_values()
    if all_mqtt:
        print()
        print("  Data coverage: All MQTT keys from DeviceMSG payload are listed; nothing dropped.")
        print("  --- All MQTT keys (from DeviceMSG payload) ---")
        for k in sorted(all_mqtt.keys()):
            if _is_sensitive_key(k):
                s = "***REDACTED***"
            else:
                v = all_mqtt[k]
                if v is None:
                    s = ""
                elif isinstance(v, (dict, list)):
                    s = json.dumps(v)
                else:
                    s = str(v)
            print(f"  {k}: {s}")


# ----- Commands that use _with_client -----
async def _run_battery(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        status = await asyncio.wait_for(client.get_status(), timeout=args.timeout)
        print(f"{status.battery}%" if status and status.battery is not None else "?")
        break


async def _run_telemetry(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        print("Streaming telemetry (Ctrl+C to stop)...")
        try:
            async for t in client.watch_telemetry():
                bat = t.battery if t.battery is not None else "?"
                state = t.state or t.working_state or "?"
                print(f"  Battery: {bat}%  State: {state}")
        except asyncio.CancelledError:
            break
        break


async def _run_lights_on(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.lights_on()
        print("Lights on.")
        break


async def _run_lights_off(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.lights_off()
        print("Lights off.")
        break


async def _run_buzzer(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.buzzer(state=0 if getattr(args, "stop", False) else 1)
        print("Buzzer stopped." if getattr(args, "stop", False) else "Buzzer on.")
        break


async def _run_chute(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.set_chute(vel=args.vel)
        print(f"Chute set to {args.vel}.")
        break


async def _run_return_to_dock(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        result = await client.return_to_dock()
        print("Return to dock sent.", result)
        break


async def _run_plans(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        plans = await asyncio.wait_for(client.list_plans(), timeout=args.timeout)
        for p in plans:
            print(f"  {p.plan_id}: {p.plan_name}")
        if not plans:
            print("No plans.")
        break


async def _run_plan_start(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        result = await client.start_plan(plan_id=args.plan_id)
        print("Plan started.", result)
        break


async def _run_plan_stop(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        result = await client.stop_plan()
        print("Plan stopped.", result)
        break


async def _run_plan_pause(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        result = await client.pause_plan()
        print("Plan paused.", result)
        break


async def _run_plan_resume(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        result = await client.resume_plan()
        print("Plan resumed.", result)
        break


async def _run_schedules(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        schedules = await asyncio.wait_for(client.list_schedules(), timeout=args.timeout)
        for s in schedules:
            print(f"  {s.schedule_id}: {s}")
        if not schedules:
            print("No schedules.")
        break


async def _run_manual_start(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.start_manual_drive()
        print("Manual drive started.")
        break


async def _run_velocity(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.set_velocity(linear=args.linear, angular=args.angular)
        print(f"Velocity set: linear={args.linear}, angular={args.angular}.")
        break


async def _run_roller(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        await client.set_roller(speed=args.speed)
        print(f"Roller speed set to {args.speed}.")
        break


async def _run_manual_stop(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        hard = args.mode == "idle"
        emergency = args.mode == "emergency"
        await client.stop_manual_drive(hard=hard, emergency=emergency)
        print(f"Manual drive stopped ({args.mode}).")
        break


async def _run_global_params(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        params = await asyncio.wait_for(client.get_global_params(), timeout=args.timeout)
        print(json.dumps(params, indent=2))
        break


async def _run_map(args: argparse.Namespace) -> None:
    async for client, _ in _with_client(args):
        data = await asyncio.wait_for(client.get_map(), timeout=args.timeout)
        out = getattr(args, "out", None)
        s = json.dumps(data, indent=2)
        if out:
            with Path(out).open("w") as f:
                f.write(s)
            print(f"Map written to {out}.")
        else:
            print(s)
        break


def main() -> None:
    _main()
