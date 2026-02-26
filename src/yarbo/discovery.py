"""
yarbo.discovery — Auto-discovery of Yarbo robots on the local network.

Attempts to discover Yarbo EMQX brokers by:
1. MQTT sniffing — connecting to a known/guessed broker and listening for
   ``snowbot/+/device/data_feedback`` messages.
2. Network scan — probing port 1883 on the local subnet.

Local broker default: 192.168.1.24:1883 (observed in production).

Reference:
    yarbo-reversing/yarbo/mqtt.py — MQTT_BROKER constant
    yarbo-reversing/scripts/local_ctrl.py — DEFAULT_BROKER
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Well-known local broker IPs observed in the wild.
KNOWN_BROKER_IPS: list[str] = [
    "192.168.1.24",   # confirmed from live capture
    "192.168.8.8",    # Yarbo AP mode default gateway
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
    for ``snowbot/+/device/data_feedback`` messages to extract the serial
    number.

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
            import ipaddress

            network = ipaddress.ip_network(subnet, strict=False)
            candidates.extend(str(host) for host in network.hosts())
        except ValueError as exc:
            logger.warning("Invalid subnet %r: %s", subnet, exc)

    # Deduplicate preserving order
    seen: set[str] = set()
    unique_candidates = [c for c in candidates if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]

    results: list[DiscoveredRobot] = []
    tasks = [_probe_broker(ip, port, timeout) for ip in unique_candidates]
    probed = await asyncio.gather(*tasks, return_exceptions=True)

    for ip, result in zip(unique_candidates, probed):
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
    except (OSError, asyncio.TimeoutError) as exc:
        raise OSError(f"Port {host}:{port} unreachable") from exc

    logger.debug("TCP port open on %s:%d — trying MQTT sniff", host, port)

    # Try MQTT sniff to get SN
    sn = await _sniff_sn(host, port, timeout)
    return DiscoveredRobot(broker_host=host, broker_port=port, sn=sn)


async def _sniff_sn(host: str, port: int, timeout: float) -> str:
    """
    Connect to the MQTT broker anonymously and listen for robot messages.

    Subscribes to ``snowbot/+/device/data_feedback`` and extracts the SN
    from the first message topic.

    Returns:
        Serial number string, or ``""`` if none arrived within timeout.
    """
    sn_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    try:
        import paho.mqtt.client as mqtt

        loop = asyncio.get_running_loop()

        def on_connect(client: mqtt.Client, ud: object, flags: object, rc: int, props: object) -> None:
            if rc == 0:
                client.subscribe("snowbot/+/device/data_feedback", qos=0)

        def on_message(client: mqtt.Client, ud: object, msg: mqtt.MQTTMessage) -> None:
            # Topic: snowbot/{SN}/device/data_feedback
            parts = msg.topic.split("/")
            if len(parts) >= 2 and not sn_future.done():
                loop.call_soon_threadsafe(sn_future.set_result, parts[1])

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="",  # anonymous
        )
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect_async(host, port, keepalive=10)
        client.loop_start()

        try:
            return await asyncio.wait_for(sn_future, timeout=timeout)
        except asyncio.TimeoutError:
            return ""
        finally:
            client.loop_stop()
            client.disconnect()

    except ImportError:
        logger.debug("paho-mqtt not installed — cannot sniff SN")
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("MQTT sniff error on %s:%d: %s", host, port, exc)
        return ""
