#!/usr/bin/env python3
"""
telemetry_stream.py — Stream live telemetry from a Yarbo robot.

Usage:
    python examples/telemetry_stream.py --broker 192.168.1.24 --sn 24400102L8HO5227
    python examples/telemetry_stream.py --broker 192.168.1.24 --sn ... --count 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from yarbo import YarboClient

logging.basicConfig(level=logging.WARNING)


async def main(broker: str, sn: str, count: int) -> None:
    print(f"\n📡 Streaming telemetry from {broker} (sn={sn})")
    print(f"   Receiving up to {count} messages. Ctrl+C to stop.\n")
    print(f"{'#':>4}  {'Battery':>8}  {'State':>12}  {'Heading':>8}  {'Speed':>8}")
    print("-" * 50)

    received = 0
    async with YarboClient(broker=broker, sn=sn) as client:
        async for telemetry in client.watch_telemetry():
            received += 1
            print(
                f"{received:>4}  "
                f"{str(telemetry.battery) + '%':>8}  "
                f"{str(telemetry.state):>12}  "
                f"{str(telemetry.heading) + '°':>8}  "
                f"{str(telemetry.speed) + 'm/s':>8}"
            )
            if received >= count:
                break

    print(f"\n✅ Received {received} telemetry messages.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stream Yarbo telemetry")
    ap.add_argument("--broker", default="192.168.1.24", help="MQTT broker IP")
    ap.add_argument("--sn", required=True, help="Robot serial number")
    ap.add_argument("--count", type=int, default=20, help="Number of messages to receive")
    args = ap.parse_args()

    try:
        asyncio.run(main(broker=args.broker, sn=args.sn, count=args.count))
    except KeyboardInterrupt:
        print("\nStopped.")
