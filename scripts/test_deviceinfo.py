#!/usr/bin/env python3
"""
Test subscribing to snowbot/{sn}/device/deviceinfo_feedback and sending deviceinfo request.

Tries multiple command name variants (get_deviceinfo, read_deviceinfo, etc.).

Usage:
    uv run python scripts/test_deviceinfo.py --broker <rover-ip> [--sn YOUR_SERIAL]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from yarbo.const import LOCAL_PORT, TOPIC_LEAF_DEVICE_INFO
from yarbo.mqtt import MqttTransport

# Command name variants to try (feedback topic is deviceinfo_feedback).
DEVICEINFO_CMD_VARIANTS = [
    "get_deviceinfo",
    "read_deviceinfo",
    "get_device_info",
    "read_device_info",
    "deviceinfo",
    "get_deviceInfo",  # camelCase
]


async def try_cmd(
    transport: MqttTransport,
    cmd: str,
    timeout: float,
) -> dict | None:
    queue = transport.create_wait_queue()
    await transport.publish(cmd, {})
    payload = await transport.wait_for_message(
        timeout=timeout,
        feedback_leaf=TOPIC_LEAF_DEVICE_INFO,
        command_name=cmd,
        _queue=queue,
    )
    return payload


async def main(
    broker: str,
    sn: str,
    timeout: float = 5.0,
    controller_first: bool = False,
) -> None:
    print(f"Connecting to {broker}:{LOCAL_PORT} (sn={sn})")
    print(f"Subscribe: snowbot/{{sn}}/device/{TOPIC_LEAF_DEVICE_INFO}")
    print(f"Publish:   snowbot/{{sn}}/app/<cmd> with payload {{}}")
    print()

    transport = MqttTransport(broker=broker, sn=sn, port=LOCAL_PORT)
    await transport.connect()

    try:
        # Optional: acquire controller first (some commands may require it)
        if controller_first:
            from yarbo.local import YarboLocalClient
            client = YarboLocalClient(broker=broker, sn=sn)
            client._transport = transport
            await client._ensure_controller()
            print("get_controller done.\n")

        for cmd in DEVICEINFO_CMD_VARIANTS:
            print(f"Try: {cmd}...", end=" ", flush=True)
            payload = await try_cmd(transport, cmd, timeout)
            if payload is not None:
                print("OK")
                print(f"  deviceinfo_feedback ({cmd}):")
                print(json.dumps(payload, indent=2, default=str))
                return
            print("no response")

        print("\nNo variant produced deviceinfo_feedback. Listening for unsolicited...")
        deadline = asyncio.get_running_loop().time() + 4.0
        async for envelope in transport.telemetry_stream():
            if envelope.kind == TOPIC_LEAF_DEVICE_INFO:
                print("  (unsolicited) deviceinfo_feedback:", envelope.payload)
            if asyncio.get_running_loop().time() >= deadline:
                break
    finally:
        await transport.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Test get_deviceinfo MQTT command")
    ap.add_argument("--broker", default="", help="Broker host (required; use yarbo discover to find)")
    ap.add_argument("--sn", default="24400102L8HO5227", help="Robot serial number")
    ap.add_argument("--timeout", type=float, default=5.0, help="Wait timeout per cmd (s)")
    ap.add_argument("--controller-first", action="store_true", help="Send get_controller first")
    args = ap.parse_args()
    asyncio.run(main(args.broker, args.sn, args.timeout, args.controller_first))
