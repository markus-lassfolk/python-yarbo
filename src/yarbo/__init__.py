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

from .client import YarboClient
from .cloud import YarboCloudClient
from .discovery import DiscoveredRobot, discover_yarbo
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
    YarboCommandResult,
    YarboLightState,
    YarboPlan,
    YarboRobot,
    YarboSchedule,
    YarboTelemetry,
)
from ._codec import decode, encode

__all__ = [
    # Core clients
    "YarboClient",
    "YarboLocalClient",
    "YarboCloudClient",
    # Discovery
    "discover_yarbo",
    "DiscoveredRobot",
    # Models
    "YarboRobot",
    "YarboTelemetry",
    "YarboLightState",
    "YarboPlan",
    "YarboSchedule",
    "YarboCommandResult",
    # Exceptions
    "YarboError",
    "YarboConnectionError",
    "YarboTimeoutError",
    "YarboProtocolError",
    "YarboAuthError",
    "YarboTokenExpiredError",
    "YarboCommandError",
    "YarboNotControllerError",
    # Codec
    "encode",
    "decode",
    # Metadata
    "__version__",
]
