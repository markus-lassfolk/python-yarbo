"""
yarbo.mqtt — Async MQTT transport layer for the Yarbo local protocol.

Wraps ``paho-mqtt`` in an asyncio-friendly interface. The Yarbo robot
exposes a plaintext EMQX broker on port 1883; all payloads are
zlib-compressed JSON (see ``yarbo._codec``).

Protocol notes (from live captures and Blutter ASM analysis):
- Topics follow ``snowbot/{SN}/app/{cmd}`` (publish) and
  ``snowbot/{SN}/device/{feedback}`` (subscribe).
- A ``get_controller`` handshake MUST be sent before action commands.
- Commands are fire-and-forget; responses arrive on ``data_feedback``.
- All payloads are encoded with :func:`yarbo._codec.encode`.

References:
  yarbo-reversing/scripts/local_ctrl.py — working reference implementation
  yarbo-reversing/docs/COMMAND_CATALOGUE.md — full command catalogue
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from ._codec import decode, encode
from .const import (
    ALL_FEEDBACK_LEAVES,
    DEFAULT_CMD_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    MQTT_KEEPALIVE,
    TOPIC_APP_TMPL,
    TOPIC_DEVICE_TMPL,
    TOPIC_LEAF_DATA_FEEDBACK,
)
from .exceptions import YarboConnectionError, YarboTimeoutError
from .models import YarboTelemetry

logger = logging.getLogger(__name__)


class MqttTransport:
    """
    Asyncio-compatible MQTT transport for the Yarbo local broker.

    Uses paho-mqtt in its callback-based API, bridged to asyncio via
    ``loop.call_soon_threadsafe``. All public methods are coroutines.

    Example::

        transport = MqttTransport(broker="192.168.1.24", sn="24400102L8HO5227")
        await transport.connect()
        await transport.publish("get_controller", {})
        await transport.publish("light_ctrl", {"led_head": 255, "led_left_w": 255})
        async for telemetry in transport.telemetry_stream():
            print(telemetry.battery)
        await transport.disconnect()
    """

    def __init__(
        self,
        broker: str,
        sn: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._broker = broker
        self._sn = sn
        self._port = port
        self._username = username
        self._password = password
        self._connect_timeout = connect_timeout

        self._client: Any = None  # paho.mqtt.client.Client
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()
        self._message_queues: list[asyncio.Queue[dict[str, Any]]] = []

    @property
    def sn(self) -> str:
        """Robot serial number."""
        return self._sn

    @property
    def is_connected(self) -> bool:
        """True if the MQTT connection is established."""
        return self._connected.is_set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Connect to the MQTT broker and subscribe to all feedback topics.

        Raises:
            YarboConnectionError: If paho-mqtt is not installed.
            YarboTimeoutError:    If the broker does not respond within timeout.
        """
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise YarboConnectionError(
                "paho-mqtt is required: pip install 'python-yarbo[mqtt]'"
            ) from exc

        self._loop = asyncio.get_running_loop()
        self._connected.clear()

        client_id = f"python-yarbo-{self._sn}-{int(time.time())}"
        self._client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            self._client.connect(self._broker, self._port, keepalive=MQTT_KEEPALIVE)
        except OSError as exc:
            raise YarboConnectionError(
                f"Cannot connect to MQTT broker {self._broker}:{self._port}: {exc}"
            ) from exc

        self._client.loop_start()

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self._connect_timeout)
        except asyncio.TimeoutError as exc:
            self._client.loop_stop()
            raise YarboTimeoutError(
                f"Timed out waiting for MQTT connection to {self._broker}:{self._port}"
            ) from exc

        logger.info("MQTT connected to %s:%d (sn=%s)", self._broker, self._port, self._sn)

    async def disconnect(self) -> None:
        """Cleanly disconnect from the MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected.clear()
            logger.info("MQTT disconnected from %s", self._broker)

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, cmd: str, payload: dict[str, Any]) -> None:
        """
        Publish a zlib-compressed command to the robot.

        Args:
            cmd:     Topic leaf name (e.g. ``"light_ctrl"``, ``"get_controller"``).
            payload: Dict to encode and publish.

        Raises:
            YarboConnectionError: If not connected.
        """
        if not self.is_connected:
            raise YarboConnectionError("Not connected to MQTT broker. Call connect() first.")
        topic = TOPIC_APP_TMPL.format(sn=self._sn, cmd=cmd)
        encoded = encode(payload)
        self._client.publish(topic, encoded, qos=0)
        logger.debug("→ MQTT [%s] %s", topic, str(payload)[:160])

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    async def wait_for_message(
        self,
        timeout: float = DEFAULT_CMD_TIMEOUT,
        feedback_leaf: str = TOPIC_LEAF_DATA_FEEDBACK,
    ) -> dict[str, Any] | None:
        """
        Wait for the next message on a feedback topic.

        Creates a temporary queue that receives the next message matching
        the given feedback topic leaf.

        Args:
            timeout:       Maximum wait time in seconds.
            feedback_leaf: Feedback topic leaf (default: ``data_feedback``).

        Returns:
            Decoded message dict, or ``None`` on timeout.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._message_queues.append(queue)
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._message_queues.discard(queue) if hasattr(  # type: ignore[attr-defined]
                self._message_queues, "discard"
            ) else self._message_queues.remove(queue)

    async def telemetry_stream(self) -> AsyncIterator[YarboTelemetry]:
        """
        Async generator that yields :class:`~yarbo.models.YarboTelemetry` objects.

        Runs indefinitely until the transport is disconnected or the caller
        breaks the loop. The stream delivers messages from ``data_feedback``
        at approximately the robot's telemetry rate (~1 Hz).

        Example::

            async for telemetry in transport.telemetry_stream():
                print(f"Battery: {telemetry.battery}%")
                if telemetry.battery and telemetry.battery < 20:
                    break

        Yields:
            :class:`~yarbo.models.YarboTelemetry` parsed from each message.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._message_queues.append(queue)
        try:
            while self.is_connected:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield YarboTelemetry.from_dict(msg)
                except asyncio.TimeoutError:
                    continue
        finally:
            try:
                self._message_queues.remove(queue)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # paho-mqtt callbacks (called from paho thread → bridge to asyncio)
    # ------------------------------------------------------------------

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int, props: Any) -> None:
        if rc == 0:
            # Subscribe to all feedback topics
            for leaf in ALL_FEEDBACK_LEAVES:
                topic = TOPIC_DEVICE_TMPL.format(sn=self._sn, feedback=leaf)
                client.subscribe(topic, qos=0)
                logger.debug("Subscribed: %s", topic)
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
        else:
            logger.error("MQTT connect failed rc=%d", rc)

    def _on_disconnect(self, client: Any, userdata: Any, disconnect_flags: Any, rc: int, props: Any) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.clear)
        logger.warning("MQTT disconnected rc=%d", rc)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        try:
            payload = decode(msg.payload)
            logger.debug("← MQTT [%s] %s", msg.topic, str(payload)[:160])
            if self._loop and self._message_queues:
                for q in list(self._message_queues):
                    self._loop.call_soon_threadsafe(q.put_nowait, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error handling MQTT message: %s", exc)
