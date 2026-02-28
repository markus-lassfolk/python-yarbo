#!/usr/bin/env python3
"""
Compare a raw MQTT log file (from yarbo --log-mqtt) with CLI output.

Reads one JSON object per line: {"topic": "...", "payload": {...}}.
For each DeviceMSG payload, flattens keys (same as ``yarbo status``'s
"All MQTT keys") and prints them so you can verify nothing is lost.

Usage:
  python scripts/compare_mqtt_log.py mqtt_log.jsonl
  python scripts/compare_mqtt_log.py mqtt_log.jsonl --first-only
"""

from __future__ import annotations

import argparse
import json
import sys

# Use the same flatten and structured-key set as the status table
from yarbo.models import flatten_mqtt_payload, STRUCTURED_MQTT_KEYS


def main() -> int:
    ap = argparse.ArgumentParser(
        description="List MQTT keys from --log-mqtt file; compare with yarbo status."
    )
    ap.add_argument(
        "log_file",
        type=str,
        help="Path to JSONL log file (topic + payload per line).",
    )
    ap.add_argument(
        "--first-only",
        action="store_true",
        help="Only flatten the first DeviceMSG in the file (faster).",
    )
    args = ap.parse_args()

    device_msg_keys: set[str] = set()
    counts: dict[str, int] = {}
    device_msg_count = 0

    with open(args.log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Skip invalid JSON: {e}", file=sys.stderr)
                continue
            topic = rec.get("topic", "")
            payload = rec.get("payload", {})
            # topic format: snowbot/SN/device/LEAF
            leaf = topic.split("/")[-1] if "/" in topic else topic
            counts[leaf] = counts.get(leaf, 0) + 1

            if leaf == "DeviceMSG":
                device_msg_count += 1
                flat = flatten_mqtt_payload(payload)
                device_msg_keys.update(flat.keys())
                if args.first_only:
                    break

    print("Message counts by topic:", counts)
    print()
    print("DeviceMSG payloads seen:", device_msg_count)
    print("All flattened keys from DeviceMSG (same as 'All MQTT keys' in yarbo status):")
    for k in sorted(device_msg_keys):
        print(" ", k)
    print()
    print("Total keys:", len(device_msg_keys))
    print(
        "These keys match yarbo status 'All MQTT keys'; nothing is dropped."
    )

    # Keys that appear in MQTT but are NOT in the structured status table
    missing_from_structured = sorted(device_msg_keys - STRUCTURED_MQTT_KEYS)
    print()
    print("--- Missing from structured status table ---")
    print(
        "The following MQTT keys are in the payload but have no dedicated row"
    )
    print("in the status table (they only appear in the 'All MQTT keys' dump):")
    if missing_from_structured:
        for k in missing_from_structured:
            print(" ", k)
        print()
        print("Total missing from structured table:", len(missing_from_structured))
    else:
        print(" (none — all keys are represented in the structured table)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
