#!/usr/bin/env python3
"""
basic_control.py — Simple Yarbo local MQTT control example.

Usage:
    python examples/basic_control.py --broker <rover-ip> --sn YOUR_SERIAL
    python examples/basic_control.py --broker <rover-ip> --sn YOUR_SERIAL --lights-off
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from yarbo import YarboClient, YarboLightState

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")


async def main(broker: str, sn: str, lights_off: bool) -> None:
    print(f"\n🤖 Connecting to Yarbo @ {broker} (sn={sn})")

    async with YarboClient(broker=broker, sn=sn) as client:
        # Get current status
        print("\n📊 Getting robot status...")
        status = await client.get_status(timeout=5.0)
        if status:
            print(f"   Battery: {status.battery}%")
            print(f"   State:   {status.state}")
            print(f"   Heading: {status.heading}°")
        else:
            print("   (no status received — robot may be asleep)")

        # Light control
        if lights_off:
            print("\n💡 Turning lights OFF...")
            await client.lights_off()
        else:
            print("\n💡 Turning lights ON (full brightness)...")
            await client.lights_on()
            await asyncio.sleep(2)

            print("💡 Body lights only (red)...")
            await client.set_lights(YarboLightState(body_left_r=255, body_right_r=255))
            await asyncio.sleep(2)

            print("💡 Lights OFF...")
            await client.lights_off()

        # Buzzer
        print("\n🔊 Beep!")
        await client.buzzer(state=1)
        await asyncio.sleep(0.3)
        await client.buzzer(state=0)

    print("\n✅ Done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Yarbo basic local control")
    ap.add_argument(
        "--broker", required=True, help="MQTT broker IP (required; use yarbo discover to find)"
    )
    ap.add_argument("--sn", required=True, help="Robot serial number")
    ap.add_argument("--lights-off", action="store_true", help="Turn lights off instead of on")
    args = ap.parse_args()

    asyncio.run(main(broker=args.broker, sn=args.sn, lights_off=args.lights_off))
