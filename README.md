# python-yarbo

[![CI](https://github.com/markus-lassfolk/python-yarbo/actions/workflows/ci.yml/badge.svg)](https://github.com/markus-lassfolk/python-yarbo/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/python-yarbo.svg)](https://pypi.org/project/python-yarbo/)
[![Python versions](https://img.shields.io/pypi/pyversions/python-yarbo.svg)](https://pypi.org/project/python-yarbo/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Python library for **local and cloud control** of [Yarbo](https://yarbo.com/) robot
mowers and snow blowers — built from reverse-engineered protocol knowledge.

> **Status**: Alpha (0.1.0) — local MQTT control is functional and confirmed working
> on hardware. Cloud API is partially functional (JWT auth migration in progress).

## Features

- 🔌 **Local MQTT control** — no cloud account required
- 💡 **Full LED control** — 7 independent channels (head, fill, body, tail)
- 🔊 **Buzzer control**
- 🌨️ **Snow chute direction** (snow blower models)
- 📡 **Live telemetry stream** — battery, state, position, heading
- 🔍 **Auto-discovery** — finds Yarbo brokers on your local network
- ☁️ **Cloud API** — robot management, scheduling, notifications
- ⚡ **Async-first** — built on asyncio with sync wrappers for scripts
- 🏠 **Home Assistant ready** — see [`home-assistant-yarbo`](https://github.com/markus-lassfolk/home-assistant-yarbo) (coming soon)

## Requirements

- Python ≥ 3.11
- Same WiFi network as the robot (for local control)
- `paho-mqtt` ≥ 2.0 (included)
- `aiohttp` ≥ 3.9 (included)

## Installation

```bash
pip install python-yarbo
```

For cloud API features (RSA password encryption):

```bash
pip install "python-yarbo[cloud]"
```

## Quick Start

### Async (recommended)

```python
import asyncio
from yarbo import YarboClient

async def main():
    async with YarboClient(broker="192.168.1.24", sn="24400102L8HO5227") as client:
        # Get a telemetry snapshot
        status = await client.get_status()
        if status:
            print(f"Battery: {status.battery}%  State: {status.state}")

        # Light control
        await client.lights_on()
        await asyncio.sleep(2)
        await client.lights_off()

        # Buzzer
        await client.buzzer(state=1)

        # Live telemetry stream
        async for telemetry in client.watch_telemetry():
            print(f"Battery: {telemetry.battery}%  Heading: {telemetry.heading}°")
            if telemetry.battery and telemetry.battery < 20:
                print("Low battery!")
                break

asyncio.run(main())
```

### Sync (scripts / REPL)

```python
from yarbo import YarboClient

client = YarboClient.connect_sync(broker="192.168.1.24", sn="24400102L8HO5227")
client.lights_on()
client.buzzer()
client.disconnect()
```

### Auto-discovery

```python
import asyncio
from yarbo import discover_yarbo, YarboClient

async def main():
    print("Scanning for Yarbo robots...")
    robots = await discover_yarbo()

    if not robots:
        print("No robots found")
        return

    print(f"Found: {robots[0]}")
    async with YarboClient(broker=robots[0].broker_host, sn=robots[0].sn) as client:
        await client.lights_on()

asyncio.run(main())
```

### Cloud login (account management)

```python
import asyncio
from yarbo import YarboCloudClient

async def main():
    async with YarboCloudClient(
        username="your@email.com",
        password="yourpassword",
        rsa_key_path="/path/to/rsa_public_key.pem",  # from APK
    ) as client:
        robots = await client.list_robots()
        for robot in robots:
            print(f"{robot.sn}: {robot.name} (online: {robot.is_online})")

        version = await client.get_latest_version()
        print(f"App: {version['appVersion']}  Firmware: {version['firmwareVersion']}")

asyncio.run(main())
```

## API Reference

### `YarboClient` (hybrid)

| Method | Description |
|--------|-------------|
| `async with YarboClient(broker, sn)` | Connect via async context manager |
| `await client.get_status()` | Single telemetry snapshot → `YarboTelemetry` |
| `await client.watch_telemetry()` | Async generator of `YarboTelemetry` |
| `await client.lights_on()` | All LEDs → 255 |
| `await client.lights_off()` | All LEDs → 0 |
| `await client.set_lights(YarboLightState)` | Per-channel LED control |
| `await client.buzzer(state=1)` | Buzzer on (1) or off (0) |
| `await client.set_chute(vel)` | Snow chute direction |
| `await client.get_controller()` | Acquire controller role (auto-called) |
| `await client.publish_raw(cmd, payload)` | Arbitrary MQTT command |
| `await client.list_robots()` | Cloud: bound robots |
| `YarboClient.connect_sync(broker, sn)` | Sync wrapper factory |

### `YarboLocalClient` (MQTT-only)

Same interface as `YarboClient`, local only, no cloud features.

### `YarboLightState`

```python
from yarbo import YarboLightState

# All on
state = YarboLightState.all_on()

# Custom
state = YarboLightState(
    led_head=255,      # Front white
    led_left_w=128,    # Left fill white
    led_right_w=128,   # Right fill white
    body_left_r=255,   # Left body red
    body_right_r=255,  # Right body red
    tail_left_r=0,     # Left tail red
    tail_right_r=0,    # Right tail red
)
async with YarboClient(...) as client:
    await client.set_lights(state)
```

### `YarboTelemetry`

Parsed from `DeviceMSG` nested schema (`BatteryMSG`, `StateMSG`, `RTKMSG`, `CombinedOdom`).

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `battery` | `int \| None` | `BatteryMSG.capacity` | State of charge (0–100 %) |
| `state` | `str \| None` | derived | `"idle"` or `"active"` |
| `working_state` | `int \| None` | `StateMSG.working_state` | Raw state (0=idle, 1=active) |
| `charging_status` | `int \| None` | `StateMSG.charging_status` | 2 = charging/docked |
| `error_code` | `int \| str \| None` | `StateMSG.error_code` | Active fault code |
| `heading` | `float \| None` | `RTKMSG.heading` | Compass heading (degrees) |
| `position_x` | `float \| None` | `CombinedOdom.x` | Odometry X (metres) |
| `position_y` | `float \| None` | `CombinedOdom.y` | Odometry Y (metres) |
| `phi` | `float \| None` | `CombinedOdom.phi` | Odometry heading (radians) |
| `speed` | `float \| None` | flat | Current speed (m/s) |
| `raw` | `dict` | — | Complete raw DeviceMSG dict |

## Cloud vs Local

| Feature | Local MQTT | Cloud REST |
|---------|-----------|------------|
| Robot control (lights, buzzer, …) | ✅ Yes | ❌ No |
| Live telemetry | ✅ Yes | ❌ No |
| List bound robots | ❌ No | ✅ Yes |
| Account management | ❌ No | ✅ Yes |
| Robot rename / bind / unbind | ❌ No | ✅ Yes |
| Notifications | ❌ No | ✅ Yes |
| Works offline | ✅ Yes | ❌ No |
| Requires cloud account | ❌ No | ✅ Yes |

> **⚠️ Cloud MQTT not implemented.** The Yarbo backend also provides a Tencent
> TDMQ MQTT broker (`mqtt-b8rkj5da-usw-public.mqtt.tencenttdmq.com:8883`) for
> remote control without LAN access. This library does **not** implement cloud
> MQTT — there is no remote-control fallback. All robot commands go via the
> local broker only.

## Security Notes

> ⚠️ **The Yarbo local MQTT broker accepts anonymous connections without
> authentication.** Anyone on your WiFi network can connect and send commands
> to your robot.

Recommendations:
- Keep the robot on a dedicated IoT VLAN and firewall it from the internet.
- Do **not** port-forward port 1883 to the internet.
- Consider a firewall rule that allows only your home automation host to reach
  port 1883 on the robot's IP.

## Protocol Notes

This library was built from reverse-engineering the Yarbo Flutter app and
live packet captures. Key protocol facts:

- **MQTT broker**: Local EMQX at `192.168.1.24:1883` or `192.168.1.55:1883`
  (check which IP your robot uses — both have been observed in production)
- **Payload encoding**: `zlib.compress(json.dumps(payload).encode())`
  (exception: `heart_beat` topic uses plain uncompressed JSON)
- **Controller handshake**: `get_controller` must be sent before action commands
- **Topics**: `snowbot/{SN}/app/{cmd}` (publish) and
  `snowbot/{SN}/device/{feedback}` (subscribe)
- **Telemetry topic**: `DeviceMSG` (~1–2 Hz) with nested schema:
  `BatteryMSG.capacity`, `StateMSG.working_state`, `RTKMSG.heading`,
  `CombinedOdom.x/y/phi`
- **Not yet implemented**: Local REST API (port 8088) and TCP JSON (port 22220)
  are documented in `yarbo-reversing` but not implemented here

See [`yarbo-reversing`](https://github.com/markus-lassfolk/yarbo-reversing) for:
- Full [command catalogue](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/COMMAND_CATALOGUE.md)
- [Light control protocol](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/LIGHT_CTRL_PROTOCOL.md)
- [API endpoints](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/API_ENDPOINTS.md)
- [MQTT protocol reference](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/MQTT_PROTOCOL.md)

## Related Projects

| Project | Description |
|---------|-------------|
| [`yarbo-reversing`](https://github.com/markus-lassfolk/yarbo-reversing) | Protocol RE: Frida scripts, MITM setup, APK tools |
| [`PSYarbo`](https://github.com/markus-lassfolk/PSYarbo) | PowerShell module (same protocol, same architecture) |

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This library was built by reverse engineering. It is not affiliated with or endorsed by
Yarbo. Use at your own risk. Do not expose your robot's MQTT broker to the internet.
