#!/usr/bin/env python3
"""
Monitor how long the Yarbo robot keeps sending MQTT after a single get_device_msg.

Use this with all apps disconnected from the robot. The script will:
  1. Connect to the broker and subscribe to all feedback topics
  2. Send ONE get_device_msg at T=0
  3. Record every incoming message (topic leaf + timestamp) for --duration seconds
  4. Print a live log and a final summary with suggested polling interval

Usage:
  uv run python scripts/monitor_telemetry_after_get_device_msg.py --broker <ip> --sn <sn>
  uv run python scripts/monitor_telemetry_after_get_device_msg.py --broker <ip> --sn <sn> --duration 600

Requires: All Yarbo apps disconnected so only this script is requesting telemetry.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from yarbo import discover_yarbo
from yarbo.const import (
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_DEVICE_MSG,
    TOPIC_LEAF_GET_DEVICE_MSG,
    TOPIC_LEAF_HEART_BEAT,
)
from yarbo.local import YarboLocalClient


async def discover_and_run(duration_sec: float, quiet: bool) -> list | None:
    """Discover robot(s), run monitor on first. Returns None on failure."""
    print("Discovering Yarbo on local network ...")
    robots = await discover_yarbo(timeout=8.0)
    if not robots:
        print("No robot found. Ensure the robot is on the same network and powered.")
        return None
    r = robots[0]
    print(f"Using {r.broker_host} (sn={r.sn})\n")
    await run_monitor(
        broker=r.broker_host,
        sn=r.sn,
        duration_sec=duration_sec,
        quiet=quiet,
    )
    return robots


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Monitor MQTT after one get_device_msg (apps disconnected)"
    )
    ap.add_argument("--broker", help="MQTT broker IP (or use --discover)")
    ap.add_argument("--sn", help="Robot serial number (or use --discover)")
    ap.add_argument(
        "--discover",
        action="store_true",
        help="Discover robot on LAN first; use first result as broker/sn",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="How long to monitor in seconds (default 300)",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Only print summary, not each message",
    )
    args = ap.parse_args()

    if args.discover:
        robots = asyncio.run(discover_and_run(args.duration, args.quiet))
        if robots is None:
            sys.exit(1)
        sys.exit(0)

    if not args.broker:
        ap.error("--broker is required (or use --discover). Use --sn if you know it, else SN is learned from first message.)")
    asyncio.run(
        run_monitor(
            broker=args.broker,
            sn=args.sn or "",
            duration_sec=args.duration,
            quiet=args.quiet,
        )
    )


def _sn_from_topic(topic: str) -> str:
    """Extract SN from snowbot/SN/device/leaf or snowbot/SN/app/... ."""
    parts = topic.split("/")
    if len(parts) >= 2 and parts[0] == "snowbot":
        return parts[1]
    return ""


async def run_monitor(
    broker: str,
    sn: str,
    duration_sec: float,
    quiet: bool,
) -> None:
    # Per-topic: list of (monotonic_t, wall_t) for each message
    events: dict[str, list[tuple[float, float]]] = {}
    t_start = time.monotonic()
    wall_start = time.time()
    trigger_sent_at: float | None = None  # when we sent get_controller + get_device_msg

    def add(kind: str) -> None:
        m = time.monotonic()
        w = time.time()
        if kind not in events:
            events[kind] = []
        events[kind].append((m, w))

    sn_learned = sn or ""
    print(f"Connecting to {broker}" + (f" (sn={sn})" if sn else " (will learn SN from first message)") + " ...")
    print(f"Will send get_controller + get_device_msg after first message, then record for {duration_sec:.0f}s.")
    print("Ensure no other apps are connected to the robot.\n")

    client = YarboLocalClient(broker=broker, sn=sn_learned)
    await client.connect()

    if sn_learned:
        await client._ensure_controller()
        await client._transport.publish(TOPIC_LEAF_GET_DEVICE_MSG, {})
        trigger_sent_at = time.monotonic()
        print("Sent get_controller + get_device_msg at T=0\n")

    deadline = t_start + duration_sec
    msg_count = 0

    async def consume() -> None:
        nonlocal msg_count, sn_learned, trigger_sent_at
        try:
            async for envelope in client._transport.telemetry_stream():
                now = time.monotonic()
                if now >= deadline:
                    return
                kind = envelope.kind
                add(kind)
                msg_count += 1
                # Learn SN from first message if we didn't have it
                if not sn_learned:
                    sn_learned = _sn_from_topic(envelope.topic)
                    if sn_learned:
                        client._sn = sn_learned
                        client._transport._sn = sn_learned
                        print(f"Learned SN from topic: {sn_learned}\n")
                        await client._ensure_controller()
                        await client._transport.publish(TOPIC_LEAF_GET_DEVICE_MSG, {})
                        trigger_sent_at = time.monotonic()
                        print(f"Sent get_controller + get_device_msg at T+{trigger_sent_at - t_start:.1f}s\n")
                rel = now - t_start
                if not quiet:
                    print(f"  T+{rel:7.1f}s  {kind}")
        except asyncio.CancelledError:
            pass

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.sleep(duration_sec)
    finally:
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        await client.disconnect()

    # Summary
    elapsed = time.monotonic() - t_start
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if trigger_sent_at is not None:
        print(f"Trigger (get_controller + get_device_msg) sent at T+{trigger_sent_at - t_start:.1f}s")
    print(f"Monitored for {elapsed:.1f}s. Total messages: {msg_count}\n")

    for kind in (TOPIC_LEAF_DEVICE_MSG, TOPIC_LEAF_DATA_FEEDBACK, TOPIC_LEAF_HEART_BEAT):
        if kind not in events or not events[kind]:
            print(f"  {kind}: 0 messages")
            continue
        lst = events[kind]
        first_t = lst[0][0] - t_start
        last_t = lst[-1][0] - t_start
        print(f"  {kind}: {len(lst)} messages")
        print(f"    first at T+{first_t:.1f}s, last at T+{last_t:.1f}s")
        if kind == TOPIC_LEAF_DEVICE_MSG:
            if len(lst) > 1:
                stream_duration = last_t - first_t
                print(f"    DeviceMSG stream duration: {stream_duration:.1f}s")
                suggested = max(5.0, min(60.0, stream_duration / 2.0))
                print(f"    Suggested polling interval: {suggested:.0f}s (poll before stream ends)")
            else:
                print("    (Only one DeviceMSG — robot may not resume streaming; keep default 10s)")

    other = [k for k in events if k not in (TOPIC_LEAF_DEVICE_MSG, TOPIC_LEAF_DATA_FEEDBACK, TOPIC_LEAF_HEART_BEAT)]
    if other:
        print(f"\n  Other topics: {', '.join(other)}")
        for k in other:
            print(f"    {k}: {len(events[k])} messages")

    print()


if __name__ == "__main__":
    main()
    sys.exit(0)
