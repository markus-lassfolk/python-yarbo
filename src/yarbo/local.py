"""
yarbo.local — YarboLocalClient: anonymous MQTT-only local control.

Controls the Yarbo robot directly over the local EMQX broker without
requiring a cloud account. All operations are local and work offline.

Prerequisites:
- The host machine must be on the same WiFi as the robot.
- The robot's EMQX broker IP must be known (use :func:`yarbo.discover` or set explicitly).
- ``paho-mqtt`` must be installed: ``pip install 'python-yarbo'``.

Protocol notes (from live captures):
- All MQTT payloads are zlib-compressed JSON (see ``_codec``).
- ``get_controller`` MUST be sent before action commands (e.g. light_ctrl).
- Topics: ``snowbot/{SN}/app/{cmd}`` (publish) and
          ``snowbot/{SN}/device/{feedback}`` (subscribe).
- Commands are generally fire-and-forget; responses on ``data_feedback``.

Transport limitations (NOT YET IMPLEMENTED):
- Local REST API (robot LAN, port 8088) — direct HTTP REST on the robot network.
  Endpoints are unknown; requires further sniffing or SSH exploration.
- Local TCP JSON (robot LAN, port 22220) — a JSON-over-TCP protocol discovered
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
from datetime import UTC, datetime
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from .const import (
    DEFAULT_CMD_TIMEOUT,
    LOCAL_BROKER_DEFAULT,
    LOCAL_PORT,
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_DEVICE_MSG,
    TOPIC_LEAF_PLAN_FEEDBACK,
)
from .exceptions import YarboNotControllerError, YarboTimeoutError
from .models import YarboCommandResult, YarboLightState, YarboPlan, YarboSchedule, YarboTelemetry
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

        async with YarboLocalClient(broker="<rover-ip>", sn="YOUR_SERIAL") as client:
            await client.lights_on()
            await client.buzzer(state=1)
            async for telemetry in client.watch_telemetry():
                print(f"Battery: {telemetry.battery}%")

    Example (manual lifecycle)::

        client = YarboLocalClient(broker="<rover-ip>", sn="YOUR_SERIAL")
        await client.connect()
        await client.lights_on()
        await client.disconnect()

    Args:
        broker:         MQTT broker IP (from discover() or set explicitly; no default).
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
        if not broker:
            raise ValueError(
                "broker IP must be set to the robot's EMQX broker address; "
                "use yarbo.discovery.discover() to find it automatically."
            )
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

    @property
    def last_heartbeat(self) -> datetime | None:
        """UTC datetime of the last received ``heart_beat`` message, or ``None``."""
        ts = self._transport.last_heartbeat
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=UTC)

    def is_healthy(self, max_age_seconds: float = 60.0) -> bool:
        """Return ``True`` if a heartbeat was received within *max_age_seconds*.

        Args:
            max_age_seconds: Maximum acceptable age of the last heartbeat in
                             seconds (default 60.0).

        Returns:
            ``True`` when the transport is connected, a heartbeat has been
            received, and the most recent one arrived within *max_age_seconds*.
        """
        if not self.is_connected:
            return False
        ts = self._transport.last_heartbeat
        if ts is None:
            return False
        return (time.time() - ts) <= max_age_seconds

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
        envelope = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DEVICE_MSG,
            _return_envelope=True,
        )
        if envelope:
            topic = envelope.get("topic", "")
            payload = envelope.get("payload", {})
            return YarboTelemetry.from_dict(payload, topic=topic)
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
    # Schedule management
    # ------------------------------------------------------------------

    async def list_schedules(self, timeout: float = DEFAULT_CMD_TIMEOUT) -> list[YarboSchedule]:
        """Fetch the list of saved schedules from the robot.

        Sends ``read_all_schedule`` and waits for the ``data_feedback`` response.

        Args:
            timeout: Maximum wait time in seconds (default 5.0).

        Returns:
            List of :class:`~yarbo.models.YarboSchedule` objects.
            Returns an empty list on timeout.
        """
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish("read_all_schedule", {})
        except Exception:
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name="read_all_schedule",
            _queue=wait_queue,
        )
        if msg is None:
            return []
        data: dict[str, Any] = msg.get("data", {}) or {}
        schedules_raw: list[Any] = data.get("scheduleList", data.get("schedules", []))
        return [YarboSchedule.from_dict(s) for s in schedules_raw]

    async def set_schedule(self, schedule: YarboSchedule) -> YarboCommandResult:
        """Save or update a schedule on the robot.

        Args:
            schedule: :class:`~yarbo.models.YarboSchedule` to save.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("save_schedule", schedule.to_dict())

    async def delete_schedule(self, schedule_id: str) -> YarboCommandResult:
        """Delete a schedule by its ID.

        Args:
            schedule_id: UUID of the schedule to delete.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("del_schedule", {"scheduleId": schedule_id})

    # ------------------------------------------------------------------
    # Plan CRUD
    # ------------------------------------------------------------------

    async def list_plans(self, timeout: float = DEFAULT_CMD_TIMEOUT) -> list[YarboPlan]:
        """Fetch the list of saved plans from the robot.

        Sends ``read_all_plan`` and waits for the ``data_feedback`` response.

        Args:
            timeout: Maximum wait time in seconds (default 5.0).

        Returns:
            List of :class:`~yarbo.models.YarboPlan` objects.
            Returns an empty list on timeout.
        """
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish("read_all_plan", {})
        except Exception:
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name="read_all_plan",
            _queue=wait_queue,
        )
        if msg is None:
            return []
        data: dict[str, Any] = msg.get("data", {}) or {}
        plans_raw: list[Any] = data.get("planList", data.get("plans", []))
        return [YarboPlan.from_dict(p) for p in plans_raw]

    async def delete_plan(self, plan_id: str) -> YarboCommandResult:
        """Delete a plan by its ID.

        Args:
            plan_id: UUID of the plan to delete.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("del_plan", {"planId": plan_id})

    # ------------------------------------------------------------------
    # Manual drive
    # ------------------------------------------------------------------

    async def start_manual_drive(self) -> None:
        """Enter manual drive mode (``set_working_state`` state=manual).

        Fires and forgets — no response is expected for this command.
        Use :meth:`set_velocity`, :meth:`set_roller`, and :meth:`set_chute`
        to control the robot while in manual mode, then call
        :meth:`stop_manual_drive` when done.
        """
        await self._ensure_controller()
        await self._transport.publish("set_working_state", {"state": "manual"})

    async def set_velocity(self, linear: float, angular: float = 0.0) -> None:
        """Send a velocity command to the robot.

        Args:
            linear:  Linear velocity in m/s (forward positive).
            angular: Angular velocity in rad/s (counter-clockwise positive).
                     Defaults to 0.0 (straight).
        """
        await self._ensure_controller()
        await self._transport.publish("cmd_vel", {"vel": linear, "rev": angular})

    async def set_roller(self, speed: int) -> None:
        """Set the roller speed (leaf-blower/snow-blower models only).

        Args:
            speed: Roller speed in RPM (0-2000).
        """
        await self._ensure_controller()
        await self._transport.publish("cmd_roller", {"vel": speed})

    async def stop_manual_drive(
        self, hard: bool = False, emergency: bool = False
    ) -> YarboCommandResult:
        """Exit manual drive mode and stop the robot.

        Three stop modes are supported (in increasing priority):

        * ``stop_manual_drive()``              → ``dstop``   (graceful stop)
        * ``stop_manual_drive(hard=True)``     → ``dstopp``  (hard immediate stop)
        * ``stop_manual_drive(emergency=True)``→ ``emergency_stop_active``

        Args:
            hard:      Send an immediate hard stop (``dstopp``) instead of the
                       default graceful stop (``dstop``).
            emergency: Send an emergency stop (``emergency_stop_active``),
                       overrides *hard*.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` from the robot.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        cmd = "emergency_stop_active" if emergency else ("dstopp" if hard else "dstop")
        return await self._publish_and_wait(cmd, {})

    # ------------------------------------------------------------------
    # Global params
    # ------------------------------------------------------------------

    async def get_global_params(self, timeout: float = DEFAULT_CMD_TIMEOUT) -> dict[str, Any]:
        """Fetch all global robot parameters (``read_global_params``).

        Args:
            timeout: Maximum wait time in seconds (default 5.0).

        Returns:
            Dict of global parameters as returned by the robot.
            Returns an empty dict on timeout.
        """
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish("read_global_params", {})
        except Exception:
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name="read_global_params",
            _queue=wait_queue,
        )
        if msg is None:
            return {}
        data = msg.get("data", {}) or {}
        return data if isinstance(data, dict) else {"data": data}

    async def set_global_params(self, params: dict[str, Any]) -> YarboCommandResult:
        """Save global robot parameters (``cmd_save_para``).

        Args:
            params: Dict of parameter key/value pairs to save.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        return await self._publish_and_wait("cmd_save_para", params)

    # ------------------------------------------------------------------
    # Map retrieval
    # ------------------------------------------------------------------

    async def get_map(self, timeout: float = 10.0) -> dict[str, Any]:
        """Retrieve the robot's current map data (``get_map``).

        Args:
            timeout: Maximum wait time in seconds (default 10.0).

        Returns:
            Map data dict as returned by the robot.
            Returns an empty dict on timeout.
        """
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish("get_map", {})
        except Exception:
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name="get_map",
            _queue=wait_queue,
        )
        if msg is None:
            return {}
        data = msg.get("data", {}) or {}
        return data if isinstance(data, dict) else {"data": data}

    # ------------------------------------------------------------------
    # Plan creation
    # ------------------------------------------------------------------

    async def create_plan(
        self,
        name: str,
        area_ids: list[int],
        enable_self_order: bool = False,
    ) -> YarboCommandResult:
        """Create a new work plan on the robot (``save_plan``).

        Args:
            name:              Display name for the plan.
            area_ids:          List of area IDs to include.
            enable_self_order: Whether the robot should self-order the areas.
                               Defaults to ``False``.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboTimeoutError: If no acknowledgement is received.
        """
        await self._ensure_controller()
        payload: dict[str, Any] = {
            "name": name,
            "areaIds": area_ids,
            "enable_self_order": enable_self_order,
        }
        return await self._publish_and_wait("save_plan", payload)

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

            client = YarboLocalClient.connect_sync(broker="<rover-ip>", sn="YOUR_SERIAL")
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
