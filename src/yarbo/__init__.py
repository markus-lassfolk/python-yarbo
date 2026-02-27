"""
yarbo — Python library for local and cloud control of Yarbo robot mowers.

Yarbo makes autonomous snow blowers and lawn mowers controlled via local MQTT.
This library was built from reverse-engineering the Yarbo Flutter app and
probing the protocol with live hardware captures.

Quick start (async)::

    import asyncio
    from yarbo import YarboClient

    async def main():
        async with YarboClient(broker="<rover-ip>", sn="YOUR_SERIAL") as client:
            status = await client.get_status()
            print(f"Battery: {status.battery}%")
            await client.lights_on()
            await client.buzzer()

    asyncio.run(main())

Quick start (sync)::

    from yarbo import YarboClient

    client = YarboClient.connect(broker="<rover-ip>", sn="YOUR_SERIAL")
    client.lights_on()
    client.disconnect()

Auto-discovery (optional subnet; if omitted, host local networks are scanned)::

    import asyncio
    from yarbo import discover_yarbo, YarboClient

    async def main():
        robots = await discover_yarbo()  # or discover_yarbo(subnet="192.0.2.0/24")
        if robots:
            async with YarboClient(broker=robots[0].broker_host, sn=robots[0].sn) as client:
                await client.lights_on()

    asyncio.run(main())

Primary/fallback (e.g. Home Assistant): use connection_order() and try each broker
until one connects::

    from yarbo import discover, connection_order, YarboClient
    endpoints = await discover()
    for ep in connection_order(endpoints):
        try:
            async with YarboClient(broker=ep.ip, sn=ep.sn) as client:
                await client.lights_on()
            break
        except Exception:
            continue  # try next endpoint

See README.md for full documentation.
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "Markus Lassfolk"
__license__ = "MIT"

from ._codec import decode, encode
from .client import YarboClient
from .cloud import YarboCloudClient
from .cloud_mqtt import YarboCloudMqttClient
from .const import Topic
from .discovery import (
    DiscoveredRobot,
    YarboEndpoint,
    connection_order,
    discover,
    discover_yarbo,
)
from .error_reporting import init_error_reporting
from .exceptions import (
    YarboAuthError,
    YarboCommandError,
    YarboConnectionError,
    YarboError,
    YarboNotControllerError,
    YarboProtocolError,
    YarboTimeoutError,
    YarboTokenExpiredError,
)
from .local import YarboLocalClient
from .models import (
    HeadType,
    TelemetryEnvelope,
    YarboCommandResult,
    YarboLightState,
    YarboPlan,
    YarboPlanParams,
    YarboRobot,
    YarboSchedule,
    YarboTelemetry,
)

__all__ = [  # noqa: RUF022 — grouped by category, alphabetical within each
    # Version
    "__version__",
    # Codec helpers
    "decode",
    "encode",
    # Error reporting
    "init_error_reporting",
    # Discovery
    "DiscoveredRobot",
    "YarboEndpoint",
    "connection_order",
    "discover",
    "discover_yarbo",
    # Topic helper
    "Topic",
    # Models (alphabetical)
    "HeadType",
    "TelemetryEnvelope",
    "YarboCommandResult",
    "YarboLightState",
    "YarboPlan",
    "YarboPlanParams",
    "YarboRobot",
    "YarboSchedule",
    "YarboTelemetry",
    # Clients (alphabetical)
    "YarboClient",
    "YarboCloudClient",
    "YarboCloudMqttClient",
    "YarboLocalClient",
    # Exceptions (alphabetical)
    "YarboAuthError",
    "YarboCommandError",
    "YarboConnectionError",
    "YarboError",
    "YarboNotControllerError",
    "YarboProtocolError",
    "YarboTimeoutError",
    "YarboTokenExpiredError",
]

# Opt-out error reporting: enabled by default, disable via YARBO_SENTRY_DSN=""
init_error_reporting()
