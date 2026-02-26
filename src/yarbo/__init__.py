"""
yarbo — Python library for local and cloud control of Yarbo robot mowers.

Yarbo makes autonomous snow blowers and lawn mowers controlled via local MQTT.
This library was built from reverse-engineering the Yarbo Flutter app and
probing the protocol with live hardware captures.

Quick start (async)::

    import asyncio
    from yarbo import YarboClient

    async def main():
        async with YarboClient(broker="192.168.1.24", sn="24400102L8HO5227") as client:
            status = await client.get_status()
            print(f"Battery: {status.battery}%")
            await client.lights_on()
            await client.buzzer()

    asyncio.run(main())

Quick start (sync)::

    from yarbo import YarboClient

    client = YarboClient.connect(broker="192.168.1.24", sn="24400102L8HO5227")
    client.lights_on()
    client.disconnect()

Auto-discovery::

    import asyncio
    from yarbo import discover_yarbo, YarboClient

    async def main():
        robots = await discover_yarbo()
        if robots:
            async with YarboClient(broker=robots[0].broker_host, sn=robots[0].sn) as client:
                await client.lights_on()

    asyncio.run(main())

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
from .discovery import DiscoveredRobot, discover_yarbo
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
