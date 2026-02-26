"""
yarbo.discovery — Auto-discovery of Yarbo robots on the local network.

Attempts to discover Yarbo EMQX brokers by:
1. MQTT sniffing — connecting to a known/guessed broker and listening for
   ``snowbot/+/device/DeviceMSG`` or ``snowbot/+/device/data_feedback`` messages.
2. Network scan — probing port 1883 on the local subnet.

Known local broker addresses observed in production:
- ``192.168.1.24``  — confirmed in live HaLow captures
- ``192.168.1.55``  — also confirmed in live HaLow captures

Reference:
    yarbo-reversing/yarbo/mqtt.py — MQTT_BROKER constant
    yarbo-reversing/scripts/local_ctrl.py — DEFAULT_BROKER
    yarbo-reversing/docs/MQTT_PROTOCOL.md — broker address notes
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from .const import LOCAL_BROKER_DEFAULT, LOCAL_BROKER_SECONDARY

logger = logging.getLogger(__name__)

#: Maximum simultaneous broker probes during subnet scanning.
_SCAN_CONCURRENCY = 50

#: Well-known local broker IPs observed in the wild.
KNOWN_BROKER_IPS: list[str] = [
    LOCAL_BROKER_DEFAULT,  # confirmed from live HaLow capture (primary)
    LOCAL_BROKER_SECONDARY,  # confirmed from live HaLow capture (secondary)
    "192.168.8.8",  # Yarbo AP mode default gateway
    "192.168.1.1",
    "192.168.0.1",
]


@dataclass
class DiscoveredRobot:
    """A Yarbo robot discovered on the local network."""

    broker_host: str
    """IP address of the MQTT broker."""

    broker_port: int
    """MQTT broker port (typically 1883)."""

    sn: str = ""
    """Robot serial number, if discovered via MQTT sniffing."""

    def __repr__(self) -> str:
        return f"DiscoveredRobot(broker={self.broker_host}:{self.broker_port}, sn={self.sn!r})"


async def discover_yarbo(
    timeout: float = 5.0,
    port: int = 1883,
    subnet: str | None = None,
) -> list[DiscoveredRobot]:
    """
    Discover Yarbo robots on the local network.

    Strategy:
    1. Try known broker IPs (fast path — most users have a fixed broker IP).
    2. If ``subnet`` is specified, scan that CIDR for open port 1883.

    For each reachable broker, attempt a brief MQTT connection and listen
    for ``snowbot/+/device/DeviceMSG`` messages to extract the serial number.

    A semaphore caps simultaneous probes to :data:`_SCAN_CONCURRENCY` to
    avoid overwhelming the local network on large CIDRs.

    Args:
        timeout: Seconds to wait per broker probe.
        port:    MQTT port to probe (default 1883).
        subnet:  Optional subnet to scan (e.g. ``"192.168.1.0/24"``).
                 If ``None``, only known IPs are tried.

    Returns:
        List of :class:`DiscoveredRobot` instances (may be empty).

    Example::

        robots = await discover_yarbo()
        for robot in robots:
            print(robot.broker_host, robot.sn)
    """
    candidates: list[str] = list(KNOWN_BROKER_IPS)

    if subnet:
        try:
            import ipaddress  # noqa: PLC0415

            network = ipaddress.ip_network(subnet, strict=False)
            candidates.extend(str(host) for host in network.hosts())
        except ValueError as exc:
            logger.warning("Invalid subnet %r: %s", subnet, exc)

    # Deduplicate preserving order
    seen: set[str] = set()
    unique_candidates = [c for c in candidates if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]

    semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def probe_with_limit(ip: str) -> DiscoveredRobot | Exception:
        async with semaphore:
            try:
                return await _probe_broker(ip, port, timeout)
            except Exception as exc:  # noqa: BLE001
                return exc

    results: list[DiscoveredRobot] = []
    probed = await asyncio.gather(*(probe_with_limit(ip) for ip in unique_candidates))

    for ip, result in zip(unique_candidates, probed, strict=True):
        if isinstance(result, DiscoveredRobot):
            results.append(result)
        elif isinstance(result, Exception):
            logger.debug("Probe %s:%d failed: %s", ip, port, result)

    logger.info("Discovery found %d broker(s): %s", len(results), [r.broker_host for r in results])
    return results


async def _probe_broker(
    host: str,
    port: int,
    timeout: float,
) -> DiscoveredRobot:
    """
    Probe a single host:port for a Yarbo MQTT broker.

    First checks TCP reachability, then attempts MQTT subscription to
    extract the robot serial number from incoming messages.

    Raises:
        OSError: If the host is unreachable.
    """
    # Quick TCP port check
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=min(timeout, 1.5)
        )
        writer.close()
        await writer.wait_closed()
    except (TimeoutError, OSError) as exc:
        raise OSError(f"Port {host}:{port} unreachable") from exc

    logger.debug("TCP port open on %s:%d — trying MQTT sniff", host, port)

    # Try MQTT sniff to get SN
    sn = await _sniff_sn(host, port, timeout)
    return DiscoveredRobot(broker_host=host, broker_port=port, sn=sn)


async def _sniff_sn(host: str, port: int, timeout: float) -> str:
    """
    Connect to the MQTT broker anonymously and listen for robot messages.

    Subscribes to ``snowbot/+/device/DeviceMSG`` and ``snowbot/+/device/data_feedback``
    and extracts the SN from the first matching message topic.

    Returns:
        Serial number string, or ``""`` if none arrived within timeout.
    """
    try:
        import paho.mqtt.client as mqtt  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        sn_future: asyncio.Future[str] = loop.create_future()

        def on_connect(
            client: mqtt.Client,
            ud: object,
            flags: object,
            reason_code: object,
            props: object,
        ) -> None:
            # Normalise reason_code to int (paho v2 passes a ReasonCode object)
            rc = getattr(reason_code, "value", reason_code)
            if rc == 0:
                client.subscribe("snowbot/+/device/DeviceMSG", qos=0)
                client.subscribe("snowbot/+/device/data_feedback", qos=0)

        def on_message(client: mqtt.Client, ud: object, msg: mqtt.MQTTMessage) -> None:
            # Topic: snowbot/{SN}/device/{leaf}
            parts = msg.topic.split("/")
            if len(parts) >= 2 and not sn_future.done():
                loop.call_soon_threadsafe(sn_future.set_result, parts[1])

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id="",  # anonymous
        )
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect_async(host, port, keepalive=10)
        client.loop_start()

        try:
            return await asyncio.wait_for(sn_future, timeout=timeout)
        except TimeoutError:
            return ""
        finally:
            client.disconnect()
            client.loop_stop(force=True)

    except ImportError:
        logger.debug("paho-mqtt not installed — cannot sniff SN")
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("MQTT sniff error on %s:%d: %s", host, port, exc)
        return ""
