#!/usr/bin/env python3
"""
Run all read-only yarbo CLI commands to gather information and stats from MQTT.

Use this to:
- Collect status, battery, telemetry, plans, schedules, global-params, and map.
- Optionally log every raw MQTT message to a file (--log-mqtt) for comparison
  with CLI output to confirm no data is lost.

Without --broker/--sn the script discovers first, then runs commands against
the first available endpoint.

Data coverage:
- `yarbo status` prints every DeviceMSG key in the "All MQTT keys" section;
  nothing from the payload is dropped. The structured table above it shows
  the main fields; the full dump below lists every key.
- To compare: run with --log-mqtt FILE, then run `python scripts/compare_mqtt_log.py FILE`
  to list all keys seen in DeviceMSG payloads and confirm they match the CLI.

Example:
  uv run python scripts/run_all_commands.py
  uv run python scripts/run_all_commands.py --broker 192.0.2.1 --sn YOUR_SN --log-mqtt mqtt_log.jsonl
  uv run python scripts/compare_mqtt_log.py mqtt_log.jsonl
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    """Run a command; return CompletedProcess. Raises on non-zero exit if check=True."""
    return subprocess.run(
        cmd,
        timeout=timeout,
        capture_output=False,
        text=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run all read-only yarbo commands (discover, status, battery, telemetry, plans, schedules, global-params, map)."
    )
    ap.add_argument("--broker", type=str, default=None, help="Broker IP (omit to auto-discover).")
    ap.add_argument("--sn", type=str, default=None, dest="serial", help="Robot serial (omit to auto-discover).")
    ap.add_argument("--port", type=int, default=1883, help="MQTT port (default: 1883).")
    ap.add_argument("--timeout", type=float, default=10.0, help="Timeout per command (default: 10).")
    ap.add_argument(
        "--log-mqtt",
        type=str,
        default=None,
        metavar="FILE",
        help="Append every raw MQTT message to FILE for comparison with CLI output.",
    )
    ap.add_argument(
        "--telemetry-seconds",
        type=float,
        default=5.0,
        help="Seconds to stream telemetry (default: 5).",
    )
    ap.add_argument(
        "--out-dir",
        type=str,
        default=".",
        help="Directory for map output (default: current dir).",
    )
    args = ap.parse_args()

    # Use the installed yarbo console script (ensure venv is active: uv run, or PATH has yarbo).
    base = ["yarbo"]
    conn = []
    if args.broker:
        conn.extend(["--broker", args.broker])
    if args.serial:
        conn.extend(["--sn", args.serial])
    conn.extend(["--port", str(args.port), "--timeout", str(args.timeout)])
    if args.log_mqtt:
        conn.extend(["--log-mqtt", args.log_mqtt])

    commands: list[tuple[str, list[str]]] = [
        ("discover", base + ["discover", "--timeout", str(args.timeout), "--port", str(args.port)]),
        ("status", base + ["status"] + conn),
        ("battery", base + ["battery"] + conn),
        ("plans", base + ["plans"] + conn),
        ("schedules", base + ["schedules"] + conn),
        ("global-params", base + ["global-params"] + conn),
        ("map", base + ["map", "--out", f"{args.out_dir.rstrip('/')}/map.json"] + conn),
    ]

    print("=== yarbo discover ===")
    p = run(base + ["discover", "--timeout", str(args.timeout), "--port", str(args.port)])
    if p.returncode != 0:
        print("discover failed; continuing anyway.", file=sys.stderr)
    print()

    for name, cmd in commands[1:]:  # skip discover (already run)
        print(f"=== yarbo {name} ===")
        p = run(cmd, timeout=args.timeout + 5)
        if p.returncode != 0:
            print(f"Warning: {name} exited with {p.returncode}", file=sys.stderr)
        print()

    # Telemetry: run for a few seconds then stop (CLI uses Ctrl+C; we use timeout + subprocess)
    print("=== yarbo telemetry (streaming for", args.telemetry_seconds, "s) ===")
    cmd_telem = base + ["telemetry"] + conn
    try:
        subprocess.run(
            cmd_telem,
            timeout=args.telemetry_seconds + 2,
            capture_output=False,
            text=True,
        )
    except subprocess.TimeoutExpired:
        pass  # expected when we hit timeout
    print()

    if args.log_mqtt:
        print("Raw MQTT messages appended to:", args.log_mqtt)
        print("Run: python scripts/compare_mqtt_log.py", args.log_mqtt, "to list all keys and compare with CLI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
