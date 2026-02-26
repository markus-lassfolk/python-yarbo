# python-yarbo

[![PyPI version](https://img.shields.io/pypi/v/python-yarbo.svg)](https://pypi.org/project/python-yarbo/)
[![Python Versions](https://img.shields.io/pypi/pyversions/python-yarbo.svg)](https://pypi.org/project/python-yarbo/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub Issues](https://img.shields.io/github/issues/markus-lassfolk/python-yarbo.svg)](https://github.com/markus-lassfolk/python-yarbo/issues)

**python-yarbo** is a Python library for local, cloud-free control of [Yarbo](https://www.yarbo.com/) robot mowers via MQTT.

Inspired by similar libraries in the ecosystem: [`python-miio`](https://github.com/rytilahti/python-miio), [`python-roborock`](https://github.com/humbertogontijo/python-roborock), [`python-kasa`](https://github.com/python-kasa/python-kasa).

> **Protocol documentation:** [markus-lassfolk/yarbo-reversing](https://github.com/markus-lassfolk/yarbo-reversing)

---

## Installation

```bash
pip install python-yarbo
```

**Requirements:** Python 3.10+, an MQTT broker reachable on your LAN (EMQX or Mosquitto).

---

## Quick Start

```python
import asyncio
from yarbo import YarboClient

async def main():
    # Connect to your local MQTT broker
    client = YarboClient(host="192.168.1.24", port=1883)
    await client.connect()

    # Discover all Yarbo robots on the broker (auto-detect via topic scan)
    robots = await client.discover(timeout=5.0)
    print(f"Found {len(robots)} robot(s): {[r.serial for r in robots]}")

    if not robots:
        return

    robot = robots[0]
    print(f"Connecting to: {robot.serial}")

    # Get current status
    status = await robot.get_status()
    print(f"  Battery:  {status.battery_pct}%")
    print(f"  State:    {status.state}")        # mowing / docked / charging / error
    print(f"  Zone:     {status.active_zone}")
    print(f"  Position: {status.gps_lat}, {status.gps_lon}")

    # Control lights
    await robot.set_light(True)   # light on
    await asyncio.sleep(2)
    await robot.set_light(False)  # light off

    # Trigger buzzer (useful for locating the robot)
    await robot.buzz()

    # Start mowing / return to dock
    # await robot.start()
    # await robot.dock()

    await client.disconnect()

asyncio.run(main())
```

---

## API Overview

### `YarboClient`

| Method | Description |
|--------|-------------|
| `YarboClient(host, port, username, password)` | Create a client connected to your local MQTT broker |
| `await client.connect()` | Connect to the broker |
| `await client.disconnect()` | Disconnect cleanly |
| `await client.discover(timeout)` | Scan broker topics and return a list of `YarboRobot` instances |

### `YarboRobot`

| Method / Property | Description |
|-------------------|-------------|
| `robot.serial` | Robot serial number (extracted from MQTT topic) |
| `robot.mac` | MAC address (from DHCP/OUI detection) |
| `await robot.get_status()` | Fetch latest telemetry as a `YarboStatus` object |
| `await robot.set_light(on: bool)` | Turn work light on or off |
| `await robot.buzz()` | Trigger the audible buzzer |
| `await robot.start(zone=None)` | Start mowing (optionally specify a zone name) |
| `await robot.dock()` | Return to dock |
| `robot.on_telemetry(callback)` | Register a callback for real-time telemetry updates |

### `YarboStatus`

| Field | Type | Description |
|-------|------|-------------|
| `battery_pct` | `int` | Battery level (0–100) |
| `state` | `str` | `mowing`, `docked`, `charging`, `error`, `returning` |
| `active_zone` | `str \| None` | Currently active mowing zone |
| `gps_lat` | `float \| None` | GPS latitude |
| `gps_lon` | `float \| None` | GPS longitude |
| `error_code` | `int \| None` | Error code (0 = no error) |
| `raw` | `dict` | Full decoded telemetry payload |

---

## Protocol

Yarbo robots communicate over MQTT with zlib-compressed JSON payloads. The topic structure is:

```
yarbo/{serial}/heart_beat        ← telemetry (compressed)
yarbo/{serial}/command/set       ← commands
yarbo/{serial}/command/response  ← ACKs
```

Full protocol documentation, packet captures, and reverse-engineering notes are in:

👉 **[markus-lassfolk/yarbo-reversing](https://github.com/markus-lassfolk/yarbo-reversing)**

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Add tests for new functionality
4. Open a PR

---

## License

[MIT](LICENSE) © Markus Lassfolk
