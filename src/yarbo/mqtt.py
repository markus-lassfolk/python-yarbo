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
- ``heart_beat`` is plain JSON (NOT zlib); the codec fallback handles this.
- ``DeviceMSG`` is the primary telemetry topic (~1-2 Hz, zlib JSON).

References:
  yarbo-reversing/scripts/local_ctrl.py — working reference implementation
  yarbo-reversing/docs/COMMAND_CATALOGUE.md — full command catalogue
  yarbo-reversing/docs/MQTT_PROTOCOL.md — protocol reference
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import paho.mqtt.client as _paho

import contextlib

from ._codec import decode, encode
from .const import (
    ALL_FEEDBACK_LEAVES,
    DEFAULT_CMD_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    MQTT_KEEPALIVE,
    TOPIC_APP_TMPL,
    TOPIC_DEVICE_TMPL,
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_HEART_BEAT,
    Topic,
)
from .exceptions import YarboConnectionError, YarboTimeoutError
from .models import TelemetryEnvelope

logger = logging.getLogger(__name__)


class MqttTransport:
    """
    Asyncio-compatible MQTT transport for the Yarbo local broker.

    Uses paho-mqtt v2 (``CallbackAPIVersion.VERSION2``) in its callback-based
    API, bridged to asyncio via ``loop.call_soon_threadsafe``. All public
    methods are coroutines.

    Message routing
    ~~~~~~~~~~~~~~~
    All received messages are pushed into the shared ``_message_queues`` list
    as envelope dicts: ``{"topic": full_topic, "payload": decoded_dict}``.
    :meth:`wait_for_message` filters by topic leaf; :meth:`telemetry_stream`
    yields all messages as :class:`~yarbo.models.TelemetryEnvelope` objects.

    Example::

        transport = MqttTransport(broker="192.168.1.24", sn="24400102L8HO5227")
        await transport.connect()
        await transport.publish("get_controller", {})
        await transport.publish("light_ctrl", {"led_head": 255, "led_left_w": 255})
        async for envelope in transport.telemetry_stream():
            if envelope.is_telemetry:
                t = envelope.to_telemetry()
                print(t.battery)
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
        qos: int = 0,
        tls: bool = False,
        tls_ca_certs: str | None = None,
    ) -> None:
        self._broker = broker
        self._sn = sn
        self._port = port
        self._username = username
        self._password = password
        self._connect_timeout = connect_timeout
        self._qos = qos
        self._tls = tls
        self._tls_ca_certs = tls_ca_certs

        # paho Client — typed via TYPE_CHECKING import to avoid hard dependency
        self._client: _paho.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()
        # Each entry is a queue of envelope dicts: {"topic": str, "payload": dict}
        self._message_queues: list[asyncio.Queue[dict[str, Any]]] = []
        # Reconnect tracking: True after the first successful disconnect
        self._was_connected: bool = False
        # Callbacks invoked (on the asyncio loop) when the transport reconnects
        self._reconnect_callbacks: list[Callable[[], None]] = []
        # Epoch timestamp of the last received heart_beat message (None = none received yet).
        # Updated directly in _on_message (paho thread) — a float write is atomic in CPython.
        self._last_heartbeat: float | None = None

    @property
    def sn(self) -> str:
        """Robot serial number."""
        return self._sn

    @property
    def is_connected(self) -> bool:
        """True if the MQTT connection is established."""
        return self._connected.is_set()

    @property
    def last_heartbeat(self) -> float | None:
        """Unix epoch timestamp of the last received ``heart_beat`` message, or ``None``."""
        return self._last_heartbeat

    def add_reconnect_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked on the asyncio loop after a reconnect.

        A *reconnect* is any successful ``_on_connect`` that happens after the
        transport has previously been disconnected (i.e. not the initial connect).
        Duplicate callbacks are silently ignored.
        """
        if callback not in self._reconnect_callbacks:
            self._reconnect_callbacks.append(callback)

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
            import paho.mqtt.client as mqtt  # noqa: PLC0415
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
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
        )
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        if self._tls:
            import ssl  # noqa: PLC0415

            self._client.tls_set(
                ca_certs=self._tls_ca_certs,
                cert_reqs=ssl.CERT_REQUIRED if self._tls_ca_certs else ssl.CERT_NONE,
            )

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
        except TimeoutError as exc:
            # Run loop_stop in executor — it joins the paho thread and must not block the loop.
            await asyncio.get_running_loop().run_in_executor(None, self._client.loop_stop)
            raise YarboTimeoutError(
                f"Timed out waiting for MQTT connection to {self._broker}:{self._port}"
            ) from exc

        logger.info("MQTT connected to %s:%d (sn=%s)", self._broker, self._port, self._sn)

    async def disconnect(self) -> None:
        """
        Cleanly disconnect from the MQTT broker.

        Calls ``disconnect()`` first to send a clean MQTT DISCONNECT packet,
        then ``loop_stop()`` (run in a thread-pool executor so it does not
        block the asyncio event loop while joining the paho network thread).
        """
        if self._client:
            self._client.disconnect()
            # paho.loop_stop() joins the network thread — run off-loop to avoid blocking.
            await asyncio.get_running_loop().run_in_executor(None, self._client.loop_stop)
            self._connected.clear()
            logger.info("MQTT disconnected from %s", self._broker)

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, cmd: str, payload: dict[str, Any], qos: int | None = None) -> None:
        """
        Publish a zlib-compressed command to the robot.

        Args:
            cmd:     Topic leaf name (e.g. ``"light_ctrl"``, ``"get_controller"``).
            payload: Dict to encode and publish.
            qos:     QoS level (0, 1, or 2). Defaults to the transport's
                     configured QoS (typically 0 for Yarbo).

        Raises:
            YarboConnectionError: If not connected.
        """
        if not self.is_connected:
            raise YarboConnectionError("Not connected to MQTT broker. Call connect() first.")
        effective_qos = qos if qos is not None else self._qos
        topic = TOPIC_APP_TMPL.format(sn=self._sn, cmd=cmd)
        encoded = encode(payload)
        self._client.publish(topic, encoded, qos=effective_qos)  # type: ignore[union-attr]
        logger.debug("→ MQTT [%s] %s", topic, str(payload)[:160])

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    def release_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """
        Remove a pre-registered wait queue from the message queue list.

        Call this if a publish fails after :meth:`create_wait_queue` but before
        :meth:`wait_for_message` — otherwise the queue leaks and accumulates
        copies of every future incoming message indefinitely.
        """
        with contextlib.suppress(ValueError):
            self._message_queues.remove(queue)

    def create_wait_queue(self) -> asyncio.Queue[dict[str, Any]]:
        """
        Pre-register a bounded message queue **before** publishing a command.

        Call this immediately before :meth:`publish` to eliminate the
        publish/subscribe race: if the robot's response arrives between the
        publish and the first ``await`` in :meth:`wait_for_message`, it is
        already captured in the returned queue.

        The returned queue must be passed back to :meth:`wait_for_message`
        via the ``_queue`` parameter.  It is automatically deregistered when
        :meth:`wait_for_message` returns.

        Returns:
            A pre-registered :class:`asyncio.Queue` (maxsize=1000).
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._message_queues.append(queue)
        return queue

    async def wait_for_message(
        self,
        timeout: float = DEFAULT_CMD_TIMEOUT,
        feedback_leaf: str = TOPIC_LEAF_DATA_FEEDBACK,
        command_name: str | None = None,
        _queue: asyncio.Queue[dict[str, Any]] | None = None,
        _return_envelope: bool = False,
    ) -> dict[str, Any] | None:
        """
        Wait for the next message matching a specific feedback topic leaf.

        If ``_queue`` is provided it must have been obtained via
        :meth:`create_wait_queue` **before** the publish call so that no
        response can be missed.  Otherwise a new queue is created here
        (subject to the usual publish/subscribe race).

        Args:
            timeout:       Maximum wait time in seconds.
            feedback_leaf: Feedback topic leaf to match (default: ``data_feedback``).
                           Use ``TOPIC_LEAF_DEVICE_MSG`` for telemetry data.
            command_name:  When set, only accept payloads whose ``topic`` field
                           equals this value.  Prevents misrouting when multiple
                           commands are in-flight on the same ``data_feedback``
                           topic.
            _queue:        Pre-registered queue from :meth:`create_wait_queue`.
                           When supplied the queue is NOT created here and will
                           be deregistered on return.
            _return_envelope: If ``True``, return the full envelope dict instead
                           of just the payload.

        Returns:
            Decoded message payload dict (or envelope dict if ``_return_envelope``
            is ``True``), or ``None`` on timeout.
        """
        if _queue is not None:
            queue = _queue
        else:
            queue = asyncio.Queue(maxsize=1000)
            self._message_queues.append(queue)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    return None
                if Topic.leaf(envelope.get("topic", "")) != feedback_leaf:
                    continue
                payload_topic = envelope.get("payload", {}).get("topic")
                if command_name is not None and payload_topic != command_name:
                    continue
                if _return_envelope:
                    return envelope
                return cast("dict[str, Any]", envelope["payload"])
        finally:
            with contextlib.suppress(ValueError):
                self._message_queues.remove(queue)

    async def telemetry_stream(self) -> AsyncIterator[TelemetryEnvelope]:
        """
        Async generator that yields :class:`~yarbo.models.TelemetryEnvelope` objects.

        Streams ALL incoming MQTT messages (``DeviceMSG``, ``heart_beat``,
        ``data_feedback``, etc.) as typed envelopes. Callers can inspect
        ``envelope.kind`` to route messages.

        Runs indefinitely until the transport is disconnected or the caller
        breaks the loop.

        Example::

            async for envelope in transport.telemetry_stream():
                if envelope.is_telemetry:         # DeviceMSG
                    t = envelope.to_telemetry()
                    print(f"Battery: {t.battery}%")
                elif envelope.is_heartbeat:        # heart_beat
                    print("Working:", envelope.payload.get("working_state"))
                if some_condition:
                    break

        Yields:
            :class:`~yarbo.models.TelemetryEnvelope` for each received message.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._message_queues.append(queue)
        try:
            while self.is_connected:
                try:
                    envelope_dict = await asyncio.wait_for(queue.get(), timeout=5.0)
                    topic: str = envelope_dict.get("topic", "")
                    payload: dict[str, Any] = envelope_dict.get("payload", {})
                    kind = Topic.leaf(topic)
                    yield TelemetryEnvelope(kind=kind, payload=payload, topic=topic)
                except TimeoutError:
                    continue
        finally:
            with contextlib.suppress(ValueError):
                self._message_queues.remove(queue)

    # ------------------------------------------------------------------
    # paho-mqtt callbacks (called from paho thread → bridge to asyncio)
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        props: Any,
    ) -> None:
        """
        paho-mqtt v2 on_connect callback.

        ``reason_code`` is a ``ReasonCode`` object under paho v2.
        ``getattr(..., "value", ...)`` normalises it to an ``int``
        so the ``rc == 0`` check works for both object and int forms.
        """
        rc = getattr(reason_code, "value", reason_code)
        if rc == 0:
            is_reconnect = self._was_connected
            # Always re-subscribe to all feedback topics (covers both initial connect
            # and automatic broker reconnections initiated by paho).
            for leaf in ALL_FEEDBACK_LEAVES:
                topic = TOPIC_DEVICE_TMPL.format(sn=self._sn, feedback=leaf)
                client.subscribe(topic, qos=self._qos)
                logger.debug("Subscribed: %s", topic)
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
                if is_reconnect:
                    logger.info("MQTT reconnected — re-subscribed (sn=%s)", self._sn)
                    for cb in list(self._reconnect_callbacks):
                        self._loop.call_soon_threadsafe(cb)
        else:
            logger.error("MQTT connect failed rc=%s", rc)

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        props: Any,
    ) -> None:
        """paho-mqtt v2 on_disconnect callback."""
        rc = getattr(reason_code, "value", reason_code)
        self._was_connected = True  # next _on_connect is a reconnect
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.clear)
        logger.warning("MQTT disconnected rc=%s", rc)

    def _enqueue_safe(self, q: asyncio.Queue[dict[str, Any]], envelope: dict[str, Any]) -> None:
        """
        Enqueue *envelope* into *q*, dropping the oldest item if the queue is full.

        Must be called **on the asyncio event loop** (via
        ``loop.call_soon_threadsafe``).  Bounded queues (maxsize=1000) prevent
        unbounded memory growth for slow consumers while preserving the newest
        real-time data.
        """
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()  # discard oldest
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(envelope)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """
        paho-mqtt on_message callback.

        Pushes an envelope dict ``{"topic": str, "payload": dict}`` into
        every registered queue so that :meth:`wait_for_message` can filter
        by topic leaf and :meth:`telemetry_stream` can expose the kind.

        Also tracks the timestamp of ``heart_beat`` messages for
        :attr:`last_heartbeat`.
        """
        try:
            payload = decode(msg.payload)
            logger.debug(
                "← MQTT [%s] %s",
                msg.topic,
                str(payload)[:160],
            )
            # Track heartbeat reception time (float write is atomic in CPython).
            if Topic.leaf(msg.topic) == TOPIC_LEAF_HEART_BEAT:
                self._last_heartbeat = time.time()
            if self._loop and self._message_queues:
                for q in list(self._message_queues):
                    # Each consumer gets its own deep copy so that no two consumers
                    # can accidentally mutate each other's view of the envelope.
                    envelope: dict[str, Any] = {
                        "topic": msg.topic,
                        "payload": copy.deepcopy(payload),
                    }
                    # _enqueue_safe runs on the event loop: drops the oldest item
                    # when the bounded queue is full so slow consumers never stall
                    # real-time telemetry delivery.
                    self._loop.call_soon_threadsafe(self._enqueue_safe, q, envelope)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error handling MQTT message on %s: %s", getattr(msg, "topic", "?"), exc)
