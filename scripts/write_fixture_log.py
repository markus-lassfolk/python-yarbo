#!/usr/bin/env python3
"""Write one DeviceMSG line to a JSONL file from the conftest fixture (for testing compare_mqtt_log without a robot)."""
from __future__ import annotations

import json
import sys

# Fixture payload from tests/conftest.py sample_telemetry_dict
SAMPLE_PAYLOAD = {
    "BatteryMSG": {
        "capacity": 83,
        "status": 3,
        "temp_err": 0,
        "timestamp": 1771943280.057,
    },
    "StateMSG": {
        "working_state": 1,
        "charging_status": 2,
        "error_code": 0,
        "machine_controller": 1,
    },
    "RTKMSG": {
        "heading": 339.4576,
        "status": "4",
        "timestamp": 1771943280.131,
    },
    "CombinedOdom": {
        "x": 1.268,
        "y": -0.338,
        "phi": -0.359,
    },
    "led": "69666",
    "timestamp": 1771943280.0,
}

if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "mqtt_fixture.jsonl"
    rec = {"topic": "snowbot/24400102L8HO5227/device/DeviceMSG", "payload": SAMPLE_PAYLOAD}
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("Wrote", out_path)
