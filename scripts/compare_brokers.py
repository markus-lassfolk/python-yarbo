#!/usr/bin/env python3
"""
Compare two Yarbo MQTT brokers (e.g. Rover vs DC from yarbo discover).

- Connects to each broker and captures telemetry (DeviceMSG) and heart_beat.
- Compares payload structure, timestamps (if present), and message timing.
- Optional: quick TCP connect test to see if both IPs behave the same.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator

from yarbo.const import LOCAL_PORT
from yarbo.models import TelemetryEnvelope, YarboTelemetry
from yarbo.mqtt import MqttTransport

# Edit for your network or pass via CLI (use yarbo discover to find Rover/DC IPs).
# Example 192.168.0.x range — replace with your Rover/DC IPs to compare two brokers.
SN = "YOUR_SERIAL"
BROKERS: list[str] = ["192.168.0.1", "192.168.0.2"]
LISTEN_SECONDS = 4.0


def tcp_probe(host: str, port: int = 1883, timeout: float = 2.0) -> dict:
    """Quick TCP connect + optional reverse DNS."""
    out: dict = {"host": host, "port": port, "connect_ok": False, "rtt_ms": None, "hostname": None}
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            out["connect_ok"] = True
            out["rtt_ms"] = round((time.perf_counter() - start) * 1000, 1)
    except OSError as e:
        out["error"] = str(e)
        return out
    try:
        out["hostname"], _, _ = socket.gethostbyaddr(host)
    except (socket.herror, socket.gaierror, OSError):
        pass
    return out


async def collect_messages(
    broker: str, count: int = 6
) -> tuple[list[tuple[float, TelemetryEnvelope]], list[tuple[float, dict]]]:
    """Connect to broker, collect up to `count` DeviceMSG and any heart_beat; return (telemetry_list, heartbeat_list) with receive timestamps."""
    telemetry: list[tuple[float, TelemetryEnvelope]] = []
    heartbeats: list[tuple[float, dict]] = []
    start = time.perf_counter()

    transport = MqttTransport(broker=broker, sn=SN, port=LOCAL_PORT)
    await transport.connect()
    try:
        async for envelope in transport.telemetry_stream():
            t = time.perf_counter() - start
            if envelope.is_telemetry:
                telemetry.append((t, envelope))
                if len(telemetry) >= count:
                    break
            elif envelope.is_heartbeat:
                heartbeats.append((t, envelope.payload))
    except asyncio.CancelledError:
        pass
    finally:
        await transport.disconnect()

    return telemetry, heartbeats


async def run_investigation() -> None:
    print("=" * 60)
    print("Yarbo broker comparison")
    print("=" * 60)
    print(f"Serial number: {SN}")
    print(f"Brokers: {BROKERS}")
    print()

    # --- TCP / network ---
    print("1. Network (TCP port 1883)")
    print("-" * 40)
    for host in BROKERS:
        r = tcp_probe(host)
        status = f"OK (RTT {r['rtt_ms']} ms)" if r.get("connect_ok") else f"FAIL — {r.get('error', '?')}"
        hostname = f"  hostname: {r['hostname']}" if r.get("hostname") else ""
        print(f"  {host}: {status}{hostname}")
    print()

    # --- Telemetry from each broker ---
    print("2. Telemetry snapshot (first few DeviceMSG per broker)")
    print("-" * 40)

    results: dict[str, tuple[list[tuple[float, TelemetryEnvelope]], list[tuple[float, dict]]]] = {}
    for broker in BROKERS:
        print(f"  Connecting to {broker}...", end=" ", flush=True)
        try:
            telemetry, heartbeats = await asyncio.wait_for(
                collect_messages(broker, count=5), timeout=LISTEN_SECONDS + 5.0
            )
            results[broker] = (telemetry, heartbeats)
            print(f"got {len(telemetry)} DeviceMSG, {len(heartbeats)} heart_beat")
        except asyncio.TimeoutError:
            print("timeout")
            results[broker] = ([], [])
        except Exception as e:
            print(f"error: {e}")
            results[broker] = ([], [])

    # Compare payload structure and timestamps
    for broker in BROKERS:
        telemetry, heartbeats = results[broker]
        if not telemetry:
            continue
        print(f"\n  [{broker}]")
        first_t, first_env = telemetry[0]
        payload = first_env.payload
        # Top-level keys (DeviceMSG nested structure)
        top_keys = sorted(payload.keys())
        print(f"    DeviceMSG top-level keys: {top_keys}")
        # Often there's a timestamp or time field somewhere
        for key in ("timestamp", "time", "ts", "timestamp_ms", "DeviceMSG"):
            if key in payload:
                val = payload[key]
                if isinstance(val, dict) and "timestamp" in val:
                    print(f"    {key}.timestamp: {val.get('timestamp')}")
                else:
                    print(f"    {key}: {val}")
        # First message parsed as YarboTelemetry
        try:
            t = first_env.to_telemetry()
            print(f"    Parsed: battery={t.battery}% state={t.state} heading={t.heading}")
        except Exception:
            pass
        # Message spacing (interval between first few)
        if len(telemetry) >= 2:
            intervals = [round((telemetry[i][0] - telemetry[i - 1][0]) * 1000) for i in range(1, len(telemetry))]
            print(f"    Intervals (ms): {intervals}")

    # --- Difference check ---
    print()
    print("3. Comparison")
    print("-" * 40)
    a_t, a_h = results.get(BROKERS[0], ([], []))
    b_t, b_h = results.get(BROKERS[1], ([], []))
    if a_t and b_t:
        p1 = a_t[0][1].payload
        p2 = b_t[0][1].payload
        keys1, keys2 = set(p1.keys()), set(p2.keys())
        if keys1 == keys2:
            print("  DeviceMSG structure: same top-level keys on both brokers")
        else:
            print(f"  DeviceMSG structure: DIFFERENT keys — only in .24: {keys1 - keys2}; only in .55: {keys2 - keys1}")
        # Compare a few key nested values (e.g. battery)
        for path in [("BatteryMSG", "capacity"), ("StateMSG", "working_state")]:
            v1 = (p1.get(path[0]) or {}).get(path[1]) if isinstance(p1.get(path[0]), dict) else None
            v2 = (p2.get(path[0]) or {}).get(path[1]) if isinstance(p2.get(path[0]), dict) else None
            if v1 is not None or v2 is not None:
                print(f"  {path[0]}.{path[1]}: .24={v1}  .55={v2}  {'(same)' if v1 == v2 else '(diff)'}")
    else:
        print("  (not enough data to compare)")
    print()


if __name__ == "__main__":
    asyncio.run(run_investigation())
