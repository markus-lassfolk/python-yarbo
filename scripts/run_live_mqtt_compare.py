#!/usr/bin/env python3
"""
Run status with MQTT logging, then compare and print what's missing from the structured table.

Use this with the robot powered on and on the same network. Optionally pass --broker and --sn
if you already know them; otherwise the CLI will discover.

Example:
  uv run python scripts/run_live_mqtt_compare.py
  uv run python scripts/run_live_mqtt_compare.py --broker 192.168.1.10 --sn 24400102L8HO5227
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run yarbo status with --log-mqtt then report missing keys."
    )
    ap.add_argument("--broker", type=str, default=None, help="Broker IP (omit to discover).")
    ap.add_argument("--sn", type=str, default=None, help="Robot serial (omit to discover).")
    ap.add_argument("--timeout", type=float, default=15.0, help="Timeout for status (default: 15).")
    ap.add_argument(
        "--log-file",
        type=str,
        default="mqtt_log.jsonl",
        help="Path for MQTT log (default: mqtt_log.jsonl).",
    )
    args = ap.parse_args()

    cmd = ["yarbo", "status", "--log-mqtt", args.log_file, "--timeout", str(args.timeout)]
    if args.broker:
        cmd.extend(["--broker", args.broker])
    if args.sn:
        cmd.extend(["--sn", args.sn])

    print("Running: ", " ".join(cmd))
    print("(Ensure the robot is on and on the same network.)")
    print()
    ret = subprocess.run(cmd, timeout=args.timeout + 30)
    if ret.returncode != 0:
        print("Status command failed. Activate the robot and try again.", file=sys.stderr)
        return ret.returncode

    print()
    print("--- Comparison (MQTT keys vs structured status table) ---")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    compare_script = os.path.join(script_dir, "compare_mqtt_log.py")
    ret2 = subprocess.run(
        [sys.executable, compare_script, args.log_file],
        timeout=10,
    )
    return ret2.returncode


if __name__ == "__main__":
    sys.exit(main())
