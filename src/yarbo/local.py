"""
yarbo.local — YarboLocalClient: anonymous MQTT-only local control.

Controls the Yarbo robot directly over the local EMQX broker without
requiring a cloud account. All operations are local and work offline.

Prerequisites:
- The host machine must be on the same WiFi as the robot.
- The robot's EMQX broker IP must be known (default: 192.168.1.24).
- ``paho-mqtt`` must be installed: ``pip install 'python-yarbo'``.

Protocol notes (from live captures):
- All MQTT payloads are zlib-compressed JSON (see ``_codec``).
- ``get_controller`` MUST be sent before action commands (e.g. light_ctrl).
- Topics: ``snowbot/{SN}/app/{cmd}`` (publish) and
          ``snowbot/{SN}/device/{feedback}`` (subscribe).
- Commands are generally fire-and-forget; responses on ``data_feedback``.

Transport limitations (NOT YET IMPLEMENTED):
- Local REST API (``192.168.8.8:8088``) — direct HTTP REST on the robot network.
  Endpoints are unknown; requires further sniffing or SSH exploration.
- Local TCP JSON (``192.168.8.1:22220``) — a JSON-over-TCP protocol discovered
  in libapp.so (uses ``com`` field with ``@n`` namespace notation).
- This module is MQTT-only. Both unimplemented transports are TODO items.

References:
    yarbo-reversing/scripts/local_ctrl.py — working reference implementation
    yarbo-reversing/docs/COMMAND_CATALOGUE.md — full command catalogue
    yarbo-reversing/docs/LIGHT_CTRL_PROTOCOL.md — light control protocol
    yarbo-reversing/docs/MQTT_PROTOCOL.md — protocol reference
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from .const import (
    DEFAULT_CMD_TIMEOUT,
    LOCAL_BROKER_DEFAULT,
    LOCAL_PORT,
    TOPIC_DEVICE_TMPL,
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_DEVICE_MSG,
    TOPIC_LEAF_PLAN_FEEDBACK,
)
from .exceptions import YarboNotControllerError, YarboTimeoutError
from .models import YarboCommandResult, YarboLightState, YarboTelemetry
from .mqtt import MqttTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

logger = logging.getLogger(__name__)


class YarboLocalClient:
    """
    Local MQTT client for anonymous control of a Yarbo robot.

    Communicates directly with the robot's on-board EMQX broker.
    No cloud account, no internet connection required.

    Example (async context manager)::

        async with YarboLocalClient(broker="192.168.1.24", sn="24400102L8HO5227") as client:
            await client.lights_on()
            await client.buzzer(state=1)
            async for telemetry in client.watch_telemetry():
                print(f"Battery: {telemetry.battery}%")

    Example (manual lifecycle)::

        client = YarboLocalClient(broker="192.168.1.24", sn="24400102L8HO5227")
        await client.connect()
        await client.lights_on()
        await client.disconnect()

    Args:
        broker:         MQTT broker IP address.
        sn:             Robot serial number.
        port:           Broker port (default 1883).
        auto_controller: If ``True`` (default), automatically send
                         ``get_controller`` before the first action command.
    """

    def __init__(
        self,
        broker: str = LOCAL_BROKER_DEFAULT,
        sn: str = "",
        port: int = LOCAL_PORT,
        auto_controller: bool = True,
    ) -> None:
        self._broker = broker
        self._sn = sn
        self._port = port
        self._auto_controller = auto_controller
        self._transport = MqttTransport(broker=broker, sn=sn, port=port)
        self._controller_acquired = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> YarboLocalClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _on_reconnect(self) -> None:
        """Reset controller state when the transport reconnects after a drop."""
        self._controller_acquired = False
        logger.info(
            "Reconnected — controller role reset, will re-acquire on next command (sn=%s)",
            self._sn,
        )

    async def connect(self) -> None:
        """Connect to the local MQTT broker."""
        self._transport.add_reconnect_callback(self._on_reconnect)
        await self._transport.connect()
        logger.info(
            "YarboLocalClient connected to %s:%d (sn=%s)",
            self._broker,
            self._port,
            self._sn,
        )

    async def disconnect(self) -> None:
        """Disconnect from the local MQTT broker."""
        await self._transport.disconnect()

    @property
    def is_connected(self) -> bool:
        """True if the MQTT connection is active."""
        return self._transport.is_connected

    @property
    def serial_number(self) -> str:
        """Robot serial number (read-only)."""
        return self._sn

    @property
    def controller_acquired(self) -> bool:
        """True if the controller handshake has been successfully completed."""
        return self._controller_acquired

    # ------------------------------------------------------------------
    # Controller handshake
    # ------------------------------------------------------------------

    async def get_controller(self) -> YarboCommandResult:
        """
        Acquire controller role for this session.

        MUST be called before any action command (lights, buzzer, motion, etc.).
        Called automatically when ``auto_controller=True`` (the default).

        Validates the ``data_feedback`` response. If the robot rejects the
        handshake (non-zero ``state``), raises :exc:`~yarbo.exceptions.YarboNotControllerError`.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboNotControllerError: If the robot explicitly rejects the handshake.
            YarboTimeoutError:       If no acknowledgement is received within the
                                     command timeout (controller flag stays ``False``).
        """
        # Pre-register the reply queue BEFORE publishing to eliminate the
        # publish/subscribe race (response could arrive before we start waiting).
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish("get_controller", {})
        except Exception:
            # publish() failed — wait_for_message's finally block never runs, so
            # we must release the pre-registered queue here to prevent a leak.
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=DEFAULT_CMD_TIMEOUT,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name="get_controller",
            _queue=wait_queue,
        )
        if msg:
            result = YarboCommandResult.from_dict(msg)
            if not result.success:
                raise YarboNotControllerError(
                    f"get_controller handshake rejected by robot "
                    f"(topic={result.topic!r}, state={result.state})",
                    code=str(result.state),
                )
            self._controller_acquired = True
            return result
        # Timeout — firmware that doesn't send data_feedback for get_controller.
        # Do NOT mark as acquired; raise so the caller can decide whether to retry.
        raise YarboTimeoutError("Timed out waiting for get_controller acknowledgement from robot.")

    async def _ensure_controller(self) -> None:
        """Send ``get_controller`` if not already acquired and auto mode is on."""
        if self._auto_controller and not self._controller_acquired:
            await self.get_controller()
            await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # Light control
    # ------------------------------------------------------------------

    async def set_lights(self, state: YarboLightState) -> None:
        """
        Set all 7 LED channels at once.

        Args:
            state: :class:`~yarbo.models.YarboLightState` with per-channel values (0-255).

        Example::

            await client.set_lights(YarboLightState(led_head=255, led_left_w=128))
        """
        await self._ensure_controller()
        await self._transport.publish("light_ctrl", state.to_dict())

    async def lights_on(self) -> None:
        """Turn all lights on at full brightness (255)."""
        await self.set_lights(YarboLightState.all_on())

    async def lights_off(self) -> None:
        """Turn all lights off."""
        await self.set_lights(YarboLightState.all_off())

    async def lights_body(self) -> None:
        """Turn on body accent lights only (red channels, others off)."""
        await self.set_lights(YarboLightState(body_left_r=255, body_right_r=255))

    # ------------------------------------------------------------------
    # Buzzer
    # ------------------------------------------------------------------

    async def buzzer(self, state: int = 1) -> None:
        """
        Trigger the robot's buzzer.

        Args:
            state: 1 to play, 0 to stop. Defaults to 1 (play).

        Example::

            await client.buzzer(state=1)   # beep
            await asyncio.sleep(0.5)
            await client.buzzer(state=0)   # stop
        """
        await self._ensure_controller()
        ts = int(time.time() * 1000)
        await self._transport.publish("cmd_buzzer", {"state": state, "timeStamp": ts})

    # ------------------------------------------------------------------
    # Chute (snow blower)
    # ------------------------------------------------------------------

    async def set_chute(self, vel: int) -> None:
        """
        Set the snow chute direction/velocity (snow blower models only).

        Args:
            vel: Chute velocity / direction integer. Positive = right, negative = left.

        Reference:
            yarbo-reversing/docs/LIGHT_CTRL_PROTOCOL.md#cmd_chute
        """
        await self._ensure_controller()
        await self._transport.publish("cmd_chute", {"vel": vel})

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    async def get_status(self, timeout: float = DEFAULT_CMD_TIMEOUT) -> YarboTelemetry | None:
        """
        Fetch a single telemetry snapshot from ``DeviceMSG`` (full telemetry).

        Waits for the next ``DeviceMSG`` message, which contains the complete
        nested telemetry payload (battery, state, RTK, odometry, etc.).

        Returns:
            :class:`~yarbo.models.YarboTelemetry` or ``None`` on timeout.
        """
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DEVICE_MSG,
        )
        if msg:
            topic = TOPIC_DEVICE_TMPL.format(sn=self._sn, feedback=TOPIC_LEAF_DEVICE_MSG)
            return YarboTelemetry.from_dict(msg, topic=topic)
        return None

    async def watch_telemetry(self) -> AsyncIterator[YarboTelemetry]:
        """
        Async generator yielding live telemetry from ``DeviceMSG`` messages.

        Filters the envelope stream to ``DeviceMSG`` messages only and yields
        a :class:`~yarbo.models.YarboTelemetry` for each one (~1-2 Hz).

        To access raw envelopes from all topics, use
        :meth:`~yarbo.mqtt.MqttTransport.telemetry_stream` on the transport
        directly.

        Example::

            async for telemetry in client.watch_telemetry():
                print(f"Battery: {telemetry.battery}%  State: {telemetry.state}")
                if telemetry.battery and telemetry.battery < 10:
                    break
        """
        # Cache plan_feedback data to merge into each DeviceMSG telemetry object
        _plan_payload: dict[str, Any] = {}
        async for envelope in self._transport.telemetry_stream():
            if envelope.kind == TOPIC_LEAF_PLAN_FEEDBACK:
                _plan_payload = envelope.payload
            elif envelope.is_telemetry:
                t = envelope.to_telemetry()
                if _plan_payload:
                    t.plan_id = _plan_payload.get("planId")
                    t.plan_state = _plan_payload.get("state")
                    t.area_covered = _plan_payload.get("areaCovered")
                    t.duration = _plan_payload.get("duration")
                yield t

    # ------------------------------------------------------------------
    # Internal helper: publish + wait for data_feedback
    # ------------------------------------------------------------------

    async def _publish_and_wait(
        self,
        cmd: str,
        payload: dict[str, Any],
        timeout: float = DEFAULT_CMD_TIMEOUT,
    ) -> YarboCommandResult:
        """Publish *cmd* and wait for the matching ``data_feedback`` response.

        Uses the pre-register pattern (create queue → publish → wait) to avoid
        the publish/subscribe race for fast-responding firmware.

        Raises:
            YarboTimeoutError: If no response arrives within *timeout* seconds.
        """
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish(cmd, payload)
        except Exception:
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name=cmd,
            _queue=wait_queue,
        )
        if msg is None:
            raise YarboTimeoutError(f"Timed out waiting for {cmd!r} response from robot.")
        return YarboCommandResult.from_dict(msg)

    # ------------------------------------------------------------------
    # Plan management
    # ------------------------------------------------------------------

    async def start_plan(self, plan_id: str) -> YarboCommandResult:
        """Start the plan identified by *plan_id*.

        Args:
            plan_id: UUID of the plan to execute.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("start_plan", {"planId": plan_id})

    async def stop_plan(self) -> YarboCommandResult:
        """Stop the currently running plan.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("stop_plan", {})

    async def pause_plan(self) -> YarboCommandResult:
        """Pause the currently running plan.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("pause_plan", {})

    async def resume_plan(self) -> YarboCommandResult:
        """Resume a paused plan.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("resume_plan", {})

    async def return_to_dock(self) -> YarboCommandResult:
        """Send the robot back to its charging dock (``cmd_recharge``).

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("cmd_recharge", {})

    # ------------------------------------------------------------------
    # Raw publish (escape hatch)
    # ------------------------------------------------------------------

    async def publish_raw(self, cmd: str, payload: dict[str, Any]) -> None:
        """
        Publish an arbitrary command to the robot.

        Useful for commands not yet wrapped in a dedicated method.

        Args:
            cmd:     Topic leaf (e.g. ``"start_plan"``).
            payload: Dict payload (will be zlib-encoded).
        """
        await self._ensure_controller()
        await self._transport.publish(cmd, payload)

    # ------------------------------------------------------------------
    # Sync wrapper
    # ------------------------------------------------------------------

    @classmethod
    def connect_sync(
        cls,
        broker: str = LOCAL_BROKER_DEFAULT,
        sn: str = "",
        port: int = LOCAL_PORT,
    ) -> _SyncYarboLocalClient:
        """
        Create a synchronous wrapper around ``YarboLocalClient``.

        Useful for scripts and REPL sessions that don't use asyncio.

        Example::

            client = YarboLocalClient.connect_sync(broker="192.168.1.24", sn="24400102...")
            client.lights_on()
            client.buzzer()
            client.disconnect()
        """
        return _SyncYarboLocalClient(broker=broker, sn=sn, port=port)


class _SyncYarboLocalClient:
    """Synchronous wrapper around :class:`YarboLocalClient`."""

    def __init__(self, broker: str, sn: str, port: int) -> None:
        self._loop = asyncio.new_event_loop()
        self._client = YarboLocalClient(broker=broker, sn=sn, port=port)
        self._loop.run_until_complete(self._client.connect())

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    def lights_on(self) -> None:
        """Turn all lights on at full brightness."""
        self._run(self._client.lights_on())

    def lights_off(self) -> None:
        """Turn all lights off."""
        self._run(self._client.lights_off())

    def buzzer(self, state: int = 1) -> None:
        """Trigger buzzer (state=1 play, state=0 stop)."""
        self._run(self._client.buzzer(state=state))

    def set_chute(self, vel: int) -> None:
        """Set chute direction/velocity."""
        self._run(self._client.set_chute(vel=vel))

    def get_status(self) -> YarboTelemetry | None:
        """Fetch a telemetry snapshot."""
        return cast("YarboTelemetry | None", self._run(self._client.get_status()))

    def publish_raw(self, cmd: str, payload: dict[str, Any]) -> None:
        """Publish an arbitrary command."""
        self._run(self._client.publish_raw(cmd, payload))

    def disconnect(self) -> None:
        """Disconnect from the broker."""
        self._run(self._client.disconnect())
        self._loop.close()
