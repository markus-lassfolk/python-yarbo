#!/usr/bin/env python3
"""
Send get_device_msg to one Yarbo broker and subscribe on both brokers.

Verifies: (1) we get the response on the broker we sent to, and (2) whether
the same message appears on the second broker (Rover vs DC).

Usage:
  uv run python scripts/test_two_brokers_telemetry.py --broker1 192.168.1.55 --broker2 192.168.1.XX
  uv run python scripts/test_two_brokers_telemetry.py --discover  # use first two discovered
"""

from __future__ import annotations

import argparse
import asyncio
import time

from yarbo import discover_yarbo
from yarbo.const import (
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_DEVICE_MSG,
    TOPIC_LEAF_GET_DEVICE_MSG,
)
from yarbo.local import YarboLocalClient


def _payload_looks_like_telemetry(payload: dict) -> bool:
    return bool(
        isinstance(payload, dict)
        and (payload.get("BatteryMSG") is not None or payload.get("StateMSG") is not None)
    )


async def run_test(
    broker1: str,
    broker2: str,
    sn: str,
    listen_seconds: float = 15.0,
) -> dict:
    """
    Connect to both brokers. Send get_controller + get_device_msg on broker1 only.
    Subscribe on both and collect data_feedback / DeviceMSG for listen_seconds.
    Returns dict with counts and whether broker2 saw the telemetry response.
    """
    received_b1: list[tuple[str, float]] = []  # (kind, t) telemetry only
    received_b2: list[tuple[str, float]] = []
    all_b1: dict[str, int] = {}  # kind -> count (all messages)
    all_b2: dict[str, int] = {}
    t0 = time.monotonic()

    def add_all(d: dict[str, int], kind: str) -> None:
        d[kind] = d.get(kind, 0) + 1

    async def collect(
        client: YarboLocalClient,
        out_list: list[tuple[str, float]],
        all_dict: dict[str, int],
    ) -> None:
        async for envelope in client._transport.telemetry_stream():
            t = time.monotonic() - t0
            if t > listen_seconds:
                return
            kind = envelope.kind
            add_all(all_dict, kind)
            if kind in (TOPIC_LEAF_DATA_FEEDBACK, TOPIC_LEAF_DEVICE_MSG):
                is_telemetry = kind == TOPIC_LEAF_DEVICE_MSG or _payload_looks_like_telemetry(
                    envelope.payload
                )
                if is_telemetry:
                    out_list.append((kind, t))

    # Connect broker1 (sender + subscriber)
    client1 = YarboLocalClient(broker=broker1, sn=sn)
    await client1.connect()
    if not client1.is_connected:
        raise RuntimeError(f"Failed to connect to broker1 {broker1}")

    # Connect broker2 (subscriber only); small delay so broker uses different client_id
    await asyncio.sleep(1.1)
    client2 = YarboLocalClient(broker=broker2, sn=sn)
    await client2.connect()
    if not client2.is_connected:
        raise RuntimeError(f"Failed to connect to broker2 {broker2}")

    # Re-check client1 still connected (broker might drop one when second connects)
    if not client1.is_connected:
        raise RuntimeError("broker1 disconnected after broker2 connect")

    # Start collectors on both
    c1 = asyncio.create_task(collect(client1, received_b1, all_b1))
    c2 = asyncio.create_task(collect(client2, received_b2, all_b2))

    # Give subscriptions a moment
    await asyncio.sleep(0.5)

    # Send only on broker1
    if not client1.is_connected:
        raise RuntimeError("broker1 disconnected before send")
    await client1._ensure_controller()
    await client1._transport.publish(TOPIC_LEAF_GET_DEVICE_MSG, {})
    send_t = time.monotonic() - t0
    print(f"  Sent get_controller + get_device_msg on {broker1} at T+{send_t:.1f}s")

    # Listen
    await asyncio.sleep(listen_seconds)
    c1.cancel()
    c2.cancel()
    for t in (c1, c2):
        try:
            await t
        except asyncio.CancelledError:
            pass

    await client1.disconnect()
    await client2.disconnect()

    return {
        "broker1": broker1,
        "broker2": broker2,
        "sn": sn,
        "listen_seconds": listen_seconds,
        "received_b1": received_b1,
        "received_b2": received_b2,
        "count_b1": len(received_b1),
        "count_b2": len(received_b2),
        "all_b1": all_b1,
        "all_b2": all_b2,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Send get_device_msg to one broker, subscribe on both; report where response appears"
    )
    ap.add_argument("--broker1", help="First broker IP (sender + subscriber)")
    ap.add_argument("--broker2", help="Second broker IP (subscriber only)")
    ap.add_argument("--sn", help="Robot serial (required if not using --discover)")
    ap.add_argument(
        "--discover",
        action="store_true",
        help="Discover robots; use first two as broker1 and broker2",
    )
    ap.add_argument("--listen", type=float, default=15.0, help="Seconds to listen (default 15)")
    args = ap.parse_args()

    if args.discover:
        print("Discovering Yarbo brokers ...")
        robots = asyncio.run(discover_yarbo(timeout=12, subnet="192.168.1.0/24"))
        # Need at least two *different* broker IPs
        ips_seen = []
        brokers = []
        for r in robots:
            if r.broker_host not in ips_seen:
                ips_seen.append(r.broker_host)
                brokers.append((r.broker_host, r.sn))
        if len(brokers) < 2:
            print(f"Found {len(robots)} endpoint(s) but only {len(brokers)} unique broker IP(s); need 2 for this test.")
            for b, s in brokers:
                print(f"  {b} sn={s}")
            exit(1)
        broker1, sn = brokers[0][0], brokers[0][1]
        broker2 = brokers[1][0]
        print(f"Using broker1={broker1} broker2={broker2} sn={sn}\n")
    else:
        if not args.broker1 or not args.broker2:
            ap.error("Need --broker1 and --broker2, or --discover")
        broker1, broker2 = args.broker1, args.broker2
        if not args.sn:
            ap.error("Need --sn when not using --discover")
        sn = args.sn

    result = asyncio.run(run_test(broker1, broker2, sn, args.listen))

    print("\n--- RESULTS ---")
    print(f"Broker1 ({result['broker1']}) — all messages: {dict(result['all_b1'])}")
    print(f"Broker1 ({result['broker1']}) — telemetry (data_feedback/DeviceMSG): {result['count_b1']}")
    for kind, t in result["received_b1"][:10]:
        print(f"  T+{t:.1f}s  {kind}")
    if result["count_b1"] > 10:
        print(f"  ... and {result['count_b1'] - 10} more")
    print(f"Broker2 ({result['broker2']}) — all messages: {dict(result['all_b2'])}")
    print(f"Broker2 ({result['broker2']}) — telemetry (data_feedback/DeviceMSG): {result['count_b2']}")
    for kind, t in result["received_b2"][:10]:
        print(f"  T+{t:.1f}s  {kind}")
    if result["count_b2"] > 10:
        print(f"  ... and {result['count_b2'] - 10} more")

    # data_feedback count (any response, not only telemetry-shaped)
    df_b1 = result["all_b1"].get("data_feedback", 0)
    df_b2 = result["all_b2"].get("data_feedback", 0)
    print("\nConclusion:")
    print(f"  Broker1 (sender) received: {df_b1} data_feedback, {result['all_b1'].get('heart_beat', 0)} heart_beat")
    print(f"  Broker2 (subscriber only) received: {df_b2} data_feedback, {result['all_b2'].get('heart_beat', 0)} heart_beat")
    if df_b1 >= 1:
        print(f"  Response seen on broker1 (sender): YES")
    else:
        print("  Response seen on broker1 (sender): NO")
    if df_b2 >= 1:
        print(f"  Response seen on broker2 (subscriber only): YES — traffic mirrored to second broker")
    else:
        print("  Response seen on broker2 (subscriber only): NO")

    # Write one-line summary for doc update
    with open("scripts/test_two_brokers_result.txt", "w") as f:
        f.write(
            f"broker1={broker1} broker2={broker2} "
            f"data_feedback_b1={result['all_b1'].get('data_feedback', 0)} "
            f"data_feedback_b2={result['all_b2'].get('data_feedback', 0)}\n"
        )


if __name__ == "__main__":
    main()
