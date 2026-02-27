"""
yarbo.discovery — Auto-discovery of Yarbo robots on the local network.

Discovers Yarbo EMQX brokers, verifies them via ``snowbot/+/device/heart_beat``,
retrieves MAC from ARP, and classifies endpoints as Rover vs DC (Data Center).
Locally administered MAC (bit 1 of first octet set) → DC; globally administered → Rover.
DC is recommended when both are present (stays connected via HaLow when Rover leaves WiFi).

Reference:
    yarbo-reversing/yarbo/mqtt.py — MQTT_BROKER constant
    yarbo-reversing/scripts/local_ctrl.py — DEFAULT_BROKER
    docs/BROKER_ROLES.md — MAC↔role reference
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import logging
from pathlib import Path
import re
import socket
import subprocess
import sys

logger = logging.getLogger(__name__)

#: Maximum simultaneous broker probes during subnet scanning.
_SCAN_CONCURRENCY = 50

#: Default cap on hosts scanned per subnet; use discover(max_hosts=N) or --max-hosts to increase.
DEFAULT_MAX_HOSTS_PER_SUBNET = 512

#: When auto-detecting local subnets, skip those larger than this (e.g. Docker /16s).
#: Only subnets with prefixlen >= this are scanned; use --subnet to scan a large range.
MIN_PREFIXLEN_AUTO = 20

#: No hardcoded IPs; use discover(subnet="...") or pass candidates. See issue #30.
KNOWN_BROKER_IPS: list[str] = []

#: DNS hostname that may indicate a DC (fast-path before full scan).
DC_HOSTNAME_HINT = "YARBO"


def _parse_linux_subnets(stdout: str) -> list[str]:
    """Parse 'ip -4 -o addr show' output into CIDR strings."""
    cidrs: list[str] = []
    for line in stdout.splitlines():
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if match:
            cidrs.append(f"{match.group(1)}/{match.group(2)}")
    return cidrs


def _parse_darwin_subnets(stdout: str) -> list[str]:
    """Parse macOS/BSD ifconfig output into CIDR strings."""
    cidrs: list[str] = []
    for block in stdout.split("\n\n"):
        inet = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)(?:/(\d+))?", block)
        if not inet:
            continue
        addr, prefix = inet.group(1), inet.group(2)
        if not prefix:
            netmask = re.search(r"netmask\s+0x([0-9a-fA-F]+)", block)
            if not netmask:
                continue
            prefix = str(bin(int(netmask.group(1), 16)).count("1"))
        cidrs.append(f"{addr}/{prefix}")
    return cidrs


def _parse_windows_subnets(stdout: str) -> list[str]:
    """Parse Windows ipconfig output into CIDR strings."""
    cidrs: list[str] = []
    lines = stdout.replace("\r", "").splitlines()
    i = 0
    while i < len(lines):
        ipv4 = re.search(r"IPv4 Address[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", lines[i])
        if ipv4:
            addr = ipv4.group(1)
            for j in range(i + 1, min(i + 5, len(lines))):
                mask_m = re.search(r"Subnet Mask[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", lines[j])
                if mask_m:
                    try:
                        prefix = ipaddress.ip_network(
                            f"{addr}/{mask_m.group(1)}", strict=False
                        ).prefixlen
                        cidrs.append(f"{addr}/{prefix}")
                    except ValueError:
                        pass
                    break
            i += 1
        i += 1
    return cidrs


def _get_local_subnets() -> list[str]:
    """
    Detect IPv4 subnets of the host's network interfaces (no loopback).

    Uses platform-specific commands so no extra dependencies are required.
    Returns a list of CIDR strings, e.g. ["192.168.1.0/24", "10.0.0.0/24"].
    """
    seen: set[str] = set()
    out: list[str] = []

    def add_network(net: str) -> None:
        try:
            network = ipaddress.ip_network(net, strict=False)
            if network.is_loopback:
                return
            cidr = str(network)
            if cidr not in seen:
                seen.add(cidr)
                out.append(cidr)
        except ValueError:
            pass

    if sys.platform == "linux":
        try:
            result = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                for cidr in _parse_linux_subnets(result.stdout):
                    add_network(cidr)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Could not get local subnets (ip): %s", e)
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["ifconfig"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                for cidr in _parse_darwin_subnets(result.stdout):
                    add_network(cidr)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Could not get local subnets (ifconfig): %s", e)
    elif sys.platform == "win32":
        try:
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                for cidr in _parse_windows_subnets(result.stdout):
                    add_network(cidr)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Could not get local subnets (ipconfig): %s", e)

    return out


def is_dc_endpoint(mac: str) -> bool:
    """
    Classify endpoint as DC (Data Center) from MAC address.

    Locally administered MACs have bit 1 of the first octet set (see IEEE 802).
    DC bridges are typically locally administered; Rover uses globally
    administered (e.g. C8:FE:0F OUI, Shenzhen Bilian).

    Args:
        mac: MAC address string, e.g. ``"9e:cd:0a:69:9e:58"`` (colons optional).

    Returns:
        True if locally administered (DC), False if globally administered (Rover)
        or if MAC cannot be parsed.
    """
    try:
        first_octet = int(mac.replace(":", "").replace("-", "")[:2], 16)
        return bool(first_octet & 0x02)
    except (ValueError, TypeError):
        return False


def _hostname_indicates_dc(hostname: str | None) -> bool:
    """True if hostname suggests a DC (e.g. contains 'YARBO'). Used when ARP gives same MAC for both."""
    if not hostname:
        return False
    return DC_HOSTNAME_HINT.upper() in hostname.upper()


@dataclass
class YarboEndpoint:
    """
    A discovered Yarbo MQTT endpoint with path classification and recommendation.

    Use :func:`discover` to get a list of endpoints. Exactly one has
    ``recommended=True`` (the DC when both Rover and DC are present; otherwise the first).
    """

    ip: str
    """Broker IP address."""

    port: int
    """Broker port (typically 1883)."""

    path: str
    """Connection path: ``"rover"`` or ``"dc"``."""

    mac: str
    """MAC address from ARP (empty if unavailable)."""

    recommended: bool
    """True for the single preferred endpoint (DC when both Rover and DC present; else first)."""

    hostname: str | None = None
    """Reverse DNS hostname, if available."""

    sn: str = ""
    """Robot serial number from MQTT topic, if discovered."""

    def __repr__(self) -> str:
        rec = " (recommended)" if self.recommended else ""
        return f"YarboEndpoint(ip={self.ip!r}, path={self.path!r}, mac={self.mac!r}{rec})"


@dataclass
class DiscoveredRobot:
    """A Yarbo robot discovered on the local network (legacy type)."""

    broker_host: str
    broker_port: int
    sn: str = ""

    def __repr__(self) -> str:
        return f"DiscoveredRobot(broker={self.broker_host}:{self.broker_port}, sn={self.sn!r})"


def _get_mac_for_ip(ip: str) -> str:
    """Return MAC address for IP from ARP table, or empty string if unavailable."""
    try:
        with Path("/proc/net/arp").open(encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0] == ip:
                mac = parts[3]
                if mac != "00:00:00:00:00:00":
                    return mac
        return ""
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["arp", "-n", ip],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
        if out.returncode == 0 and out.stdout:
            for part in out.stdout.split():
                if ":" in part and len(part) == 17:
                    return part
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _get_hostname_for_ip(ip: str) -> str | None:
    """Reverse DNS lookup for IP. Returns hostname or None."""
    try:
        name, _, _ = socket.gethostbyaddr(ip)
        return name
    except (socket.herror, socket.gaierror, OSError):
        return None


async def _verify_yarbo_heartbeat(host: str, port: int, timeout: float) -> tuple[bool, str]:
    """
    Verify host:port is a Yarbo broker by subscribing to heart_beat.
    Returns (True, sn) if {"working_state": N} received; (False, "") otherwise.
    """
    try:
        import paho.mqtt.client as mqtt  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        done: asyncio.Future[tuple[bool, str]] = loop.create_future()

        def on_connect(
            client: mqtt.Client,
            ud: object,
            flags: object,
            reason_code: object,
            props: object,
        ) -> None:
            rc = getattr(reason_code, "value", reason_code)
            if rc == 0:
                client.subscribe("snowbot/+/device/heart_beat", qos=0)

        def on_message(client: mqtt.Client, ud: object, msg: mqtt.MQTTMessage) -> None:
            if done.done():
                return
            try:
                payload = json.loads(msg.payload) if msg.payload else {}
                if "working_state" in payload:
                    parts = msg.topic.split("/")
                    sn = parts[1] if len(parts) >= 2 else ""
                    loop.call_soon_threadsafe(done.set_result, (True, sn))
            except (json.JSONDecodeError, TypeError):
                pass

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined, unused-ignore]
            client_id="",
        )
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect_async(host, port, keepalive=10)
        client.loop_start()
        try:
            return await asyncio.wait_for(done, timeout=timeout)
        except TimeoutError:
            return (False, "")
        finally:
            client.disconnect()
            await loop.run_in_executor(None, client.loop_stop)
    except ImportError:
        return (False, "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Heartbeat check %s:%d failed: %s", host, port, exc)
        return (False, "")


def _expand_subnet(
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
    max_hosts: int,
) -> tuple[list[str], bool]:
    """Expand network to host IPs, capping at max_hosts. Returns (ips, was_capped)."""
    hosts_iter = iter(network.hosts())
    ips: list[str] = []
    for _ in range(max_hosts):
        try:
            ips.append(str(next(hosts_iter)))
        except StopIteration:
            return (ips, False)
    try:
        next(hosts_iter)
    except StopIteration:
        return (ips, False)
    return (ips, True)


async def discover(
    timeout: float = 5.0,
    port: int = 1883,
    subnet: str | None = None,
    max_hosts: int = DEFAULT_MAX_HOSTS_PER_SUBNET,
) -> list[YarboEndpoint]:
    """
    Discover Yarbo MQTT endpoints and label them as Rover vs DC with recommendation.

    Scans candidates (known IPs + optional subnet), verifies each via heart_beat,
    retrieves MAC from ARP, classifies path (locally administered MAC → DC).
    Sets recommended=True for DC when both present, or for the sole endpoint.

    When subnet is omitted, the host's local IPv4 interfaces are detected.
    Only subnets with prefix /20 or smaller (e.g. /24) are scanned, so large
    ranges like Docker /16s are skipped; use --subnet to scan a specific range.
    At most max_hosts hosts are scanned per subnet; use --max-hosts to increase.

    For primary/fallback (e.g. Home Assistant), use :func:`connection_order` on
    the result and try connecting to each endpoint in order until one works.
    """
    candidates: list[str] = list(KNOWN_BROKER_IPS)
    if subnet:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
            ips, capped = _expand_subnet(network, max_hosts)
            candidates.extend(ips)
            if capped:
                total = network.num_addresses - 2
                logger.warning(
                    "Subnet %s has %d hosts; scanning first %d. Use --max-hosts to scan more.",
                    subnet,
                    total,
                    max_hosts,
                )
        except ValueError as exc:
            logger.warning("Invalid subnet %r: %s", subnet, exc)
    else:
        for net_cidr in _get_local_subnets():
            try:
                network = ipaddress.ip_network(net_cidr, strict=False)
                if network.prefixlen < MIN_PREFIXLEN_AUTO:
                    logger.debug(
                        "Skipping large subnet %s (/%d); typical of Docker/containers. Use --subnet to scan it.",
                        net_cidr,
                        network.prefixlen,
                    )
                    continue
                ips, capped = _expand_subnet(network, max_hosts)
                candidates.extend(ips)
                if capped:
                    total = network.num_addresses - 2
                    logger.warning(
                        "Subnet %s has %d hosts; scanning first %d. Use --max-hosts to scan more.",
                        net_cidr,
                        total,
                        max_hosts,
                    )
            except ValueError:
                pass
        if not candidates:
            logger.warning(
                "No subnet given and no local subnets detected; discovery will not scan any IPs"
            )

    seen: set[str] = set()
    unique_candidates = [c for c in candidates if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]

    logger.info("Scanning %d candidate(s) for Yarbo brokers (port %d)", len(unique_candidates), port)

    semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)
    endpoints: list[YarboEndpoint] = []

    async def probe_one(ip: str) -> YarboEndpoint | None:
        async with semaphore:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), timeout=min(timeout, 1.5)
                )
                writer.close()
                await writer.wait_closed()
            except (TimeoutError, OSError):
                return None
            is_yarbo, sn = await _verify_yarbo_heartbeat(ip, port, timeout)
            if not is_yarbo:
                return None
            loop = asyncio.get_running_loop()
            mac = await loop.run_in_executor(None, _get_mac_for_ip, ip)
            hostname = await loop.run_in_executor(None, _get_hostname_for_ip, ip)
            path = (
                "dc"
                if (is_dc_endpoint(mac) or _hostname_indicates_dc(hostname))
                else "rover"
            )
            return YarboEndpoint(
                ip=ip,
                port=port,
                path=path,
                mac=mac,
                recommended=False,
                hostname=hostname,
                sn=sn,
            )
        return None

    results = await asyncio.gather(
        *(probe_one(ip) for ip in unique_candidates),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, YarboEndpoint):
            endpoints.append(r)
        elif isinstance(r, Exception):
            logger.debug("Probe failed: %s", r)

    # Exactly one endpoint is recommended: prefer DC when both Rover and DC exist
    # (stays connected via HaLow when Rover leaves WiFi); otherwise the first endpoint.
    has_rover = any(e.path == "rover" for e in endpoints)
    has_dc = any(e.path == "dc" for e in endpoints)
    recommended_one: YarboEndpoint | None = None
    if len(endpoints) == 1:
        recommended_one = endpoints[0]
    elif has_dc and has_rover:
        recommended_one = next((e for e in endpoints if e.path == "dc"), endpoints[0])
    else:
        recommended_one = endpoints[0] if endpoints else None
    for e in endpoints:
        e.recommended = e is recommended_one

    logger.info(
        "Discovery found %d endpoint(s): %s",
        len(endpoints),
        [(e.ip, e.path, e.recommended) for e in endpoints],
    )
    return endpoints


def connection_order(endpoints: list[YarboEndpoint]) -> list[YarboEndpoint]:
    """
    Return endpoints in try order for primary/fallback (like primary/secondary DNS).

    Puts the recommended endpoint first, then the rest. Use when connecting so that
    if the first fails (e.g. robot out of WiFi range), the client can try the next.
    When Rover/DC classification is ambiguous (same MAC, no hostname), this
    avoids having to guess which IP to use.
    """
    recommended = next((e for e in endpoints if e.recommended), None)
    if not recommended:
        return list(endpoints)
    ordered = [recommended]
    for e in endpoints:
        if e is not recommended:
            ordered.append(e)
    return ordered


async def discover_yarbo(
    timeout: float = 5.0,
    port: int = 1883,
    subnet: str | None = None,
    max_hosts: int = DEFAULT_MAX_HOSTS_PER_SUBNET,
) -> list[DiscoveredRobot]:
    """
    Discover Yarbo robots on the local network (legacy API).

    Uses :func:`discover` and maps to :class:`DiscoveredRobot`. For path
    and recommendation use :func:`discover` and :class:`YarboEndpoint` instead.
    """
    endpoints = await discover(
        timeout=timeout, port=port, subnet=subnet, max_hosts=max_hosts
    )
    return [DiscoveredRobot(broker_host=e.ip, broker_port=e.port, sn=e.sn) for e in endpoints]


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
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined, unused-ignore]
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
            await loop.run_in_executor(None, client.loop_stop)

    except ImportError:
        logger.debug("paho-mqtt not installed — cannot sniff SN")
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("MQTT sniff error on %s:%d: %s", host, port, exc)
        return ""
