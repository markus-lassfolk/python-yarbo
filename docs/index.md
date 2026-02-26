# python-yarbo Documentation

Python library for local and cloud control of Yarbo robot mowers via MQTT.

## Navigation

- [API Reference](api.md)
- [Protocol Details](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/COMMAND_CATALOGUE.md)
- [Light Control Protocol](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/LIGHT_CTRL_PROTOCOL.md)
- [API Endpoints](https://github.com/markus-lassfolk/yarbo-reversing/blob/main/docs/API_ENDPOINTS.md)
- [CHANGELOG](../CHANGELOG.md)
- [CONTRIBUTING](../CONTRIBUTING.md)

## Quick Start

```bash
pip install python-yarbo
```

```python
import asyncio
from yarbo import YarboClient

async def main():
    async with YarboClient(broker="192.168.1.24", sn="YOUR_SERIAL") as client:
        await client.lights_on()
        status = await client.get_status()
        print(f"Battery: {status.battery}%")

asyncio.run(main())
```

## Architecture

```
YarboClient (hybrid orchestrator)
├── YarboLocalClient (MQTT-only, anonymous)
│   └── MqttTransport (paho-mqtt wrapper)
│       └── _codec (zlib+JSON encode/decode)
└── YarboCloudClient (REST + JWT, lazy)
    └── YarboAuth (login, refresh, RSA encryption)
```

All MQTT payloads use the Yarbo wire format: `zlib.compress(json.dumps(payload).encode())`.
