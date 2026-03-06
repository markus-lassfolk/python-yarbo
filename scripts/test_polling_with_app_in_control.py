#!/usr/bin/env python3
"""
Test polling + get_controller while the mobile app is in control.

Run this while you have the Yarbo app open and in control on your phone.
We will send get_controller and get_device_msg (like HASS polling) and
report whether we got control (state=0) or were rejected (state!=0).
If the app keeps working, the robot is rejecting us when app has control.

Usage:
  uv run python scripts/test_polling_with_app_in_control.py --broker 192.168.1.55 --sn 24400102L8HO5227
  uv run python scripts/test_polling_with_app_in_control.py --broker 192.168.1.55 --sn 24400102L8HO5227 --duration 90
"""

from __future__ import annotations

import argparse
import asyncio
import time

from yarbo.const import (
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_GET_DEVICE_MSG,
)
from yarbo.exceptions import YarboNotControllerError, YarboTimeoutError
from yarbo.local import YarboLocalClient
from yarbo.local import _payload_looks_like_device_msg


async def run_test(broker: str, sn: str, duration_sec: float) -> None:
    print(f"Connecting to {broker} (sn={sn}) ...")
    print("Ensure the Yarbo app is open and in control on your phone.\n")

    client = YarboLocalClient(broker=broker, sn=sn)
    await client.connect()

    # 1) Try get_controller — does robot give us control or reject (app keeps it)?
    print("1) Sending get_controller ...")
    try:
        result = await client.get_controller(timeout=5.0)
        print(f"   get_controller response: state={result.state}  ->  WE GOT CONTROL (app may lose it)\n")
    except YarboNotControllerError as e:
        print(f"   get_controller REJECTED (state={getattr(e, 'code', '?')})  ->  app keeps control\n")
    except YarboTimeoutError:
        print("   get_controller timed out (no response)\n")
        await client.disconnect()
        return
    except Exception as e:
        print(f"   get_controller failed: {e}\n")
        await client.disconnect()
        return

    # 2) Run polling: get_device_msg every 10s, wait for telemetry response each time
    print(f"2) Polling get_device_msg every 10s for {duration_sec:.0f}s, checking for status response ...")
    t0 = time.monotonic()
    sent, received = 0, 0
    while time.monotonic() - t0 < duration_sec:
        await asyncio.sleep(10.0)
        if time.monotonic() - t0 >= duration_sec:
            break
        try:
            q = client._transport.create_wait_queue()
            await client._transport.publish(TOPIC_LEAF_GET_DEVICE_MSG, {})
            sent += 1
            msg = await client._transport.wait_for_message(
                timeout=5.0,
                feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
                _queue=q,
                accept_if=_payload_looks_like_device_msg,
            )
            if msg:
                received += 1
            print(f"   T+{time.monotonic()-t0:.0f}s  get_device_msg #{sent}  ->  {'status OK' if msg else 'no response'}")
        except Exception as e:
            print(f"   get_device_msg failed: {e}")
    print(f"   -> Sent {sent}, received status {received}/{sent}\n")

    # 3) Try get_controller again — did anything change?
    print("3) Sending get_controller again ...")
    try:
        result2 = await client.get_controller(timeout=5.0)
        print(f"   get_controller response: state={result2.state}  ->  WE GOT CONTROL\n")
    except YarboNotControllerError as e:
        print(f"   get_controller REJECTED (state={getattr(e, 'code', '?')})  ->  app keeps control\n")
    except Exception as e:
        print(f"   get_controller failed: {e}\n")

    await client.disconnect()

    print("--- SUMMARY ---")
    print("If the app never lost control: robot is rejecting our get_controller when app has session (good for coexistence).")
    print("If the app lost control: our get_controller took over (bad — HASS would need to avoid get_controller when app is active).")


async def run_test_no_controller(broker: str, sn: str, duration_sec: float) -> None:
    """Poll only get_device_msg (no get_controller). Check if we get status back without taking control."""
    print(f"Connecting to {broker} (sn={sn}) ...")
    print("Polling get_device_msg ONLY (no get_controller). Checking status response each time.\n")

    client = YarboLocalClient(broker=broker, sn=sn)
    await client.connect()

    print(f"Sending get_device_msg every 10s for {duration_sec:.0f}s, checking for status response ...")
    t0 = time.monotonic()
    sent, received = 0, 0
    while time.monotonic() - t0 < duration_sec:
        try:
            q = client._transport.create_wait_queue()
            await client._transport.publish(TOPIC_LEAF_GET_DEVICE_MSG, {})
            sent += 1
            msg = await client._transport.wait_for_message(
                timeout=5.0,
                feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
                _queue=q,
                accept_if=_payload_looks_like_device_msg,
            )
            if msg:
                received += 1
            print(f"   T+{time.monotonic()-t0:.0f}s  get_device_msg #{sent}  ->  {'status OK' if msg else 'no response'}")
        except Exception as e:
            print(f"   get_device_msg failed: {e}")
        await asyncio.sleep(10.0)
    await client.disconnect()
    print(f"\n   -> Sent {sent}, received status {received}/{sent}")
    print("Done. If app kept control, telemetry-only polling (no get_controller) is safe.\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Test polling + get_controller while app is in control"
    )
    ap.add_argument("--broker", default="192.168.1.55", help="Broker IP")
    ap.add_argument("--sn", required=True, help="Robot serial number")
    ap.add_argument("--duration", type=float, default=60.0, help="Polling duration (default 60s)")
    ap.add_argument(
        "--no-controller",
        action="store_true",
        help="Only send get_device_msg (no get_controller); do not try to take control",
    )
    args = ap.parse_args()
    if args.no_controller:
        asyncio.run(run_test_no_controller(args.broker, args.sn, args.duration))
    else:
        asyncio.run(run_test(args.broker, args.sn, args.duration))


if __name__ == "__main__":
    main()
