#!/usr/bin/env python3
"""
Quick check: connect to Yarbo broker, acquire controller, request one telemetry snapshot.

Use this to verify the robot responds to get_controller + get_device_msg (e.g. for HASS).
Example: uv run python scripts/check_telemetry_connection.py --broker 192.168.1.55 --sn YOUR_SN
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from yarbo.local import YarboLocalClient


async def main(broker: str, sn: str, timeout: float) -> None:
    print(f"Connecting to {broker} (sn={sn}) ...")
    client = YarboLocalClient(broker=broker, sn=sn)
    try:
        await client.connect()
        print("Requesting one telemetry snapshot (get_controller + get_device_msg) ...")
        status = await client.get_status(timeout=timeout)
        if status is None:
            print("No response (timeout). Check broker IP and that the robot is on.")
            sys.exit(1)
        print(f"OK  battery={status.battery}%  state={status.state}  working_state={status.working_state}")
    finally:
        await client.disconnect()
    print("Done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Check Yarbo telemetry connection")
    ap.add_argument("--broker", default="192.168.1.55", help="MQTT broker IP (default 192.168.1.55)")
    ap.add_argument("--sn", required=True, help="Robot serial number")
    ap.add_argument("--timeout", type=float, default=10.0, help="Wait for response (default 10s)")
    args = ap.parse_args()
    asyncio.run(main(args.broker, args.sn, args.timeout))
