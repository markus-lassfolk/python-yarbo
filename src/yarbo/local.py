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
from .models import (
    HeadType,
    YarboCommandResult,
    YarboLightState,
    YarboPlan,
    YarboSchedule,
    YarboTelemetry,
)
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
        mqtt_log_path: str | None = None,
        debug: bool = False,
        debug_raw: bool = False,
        mqtt_capture_max: int = 0,
    ) -> None:
        self._broker = broker
        self._sn = sn
        self._port = port
        self._auto_controller = auto_controller
        self._transport = MqttTransport(broker=broker, sn=sn, port=port)
        self._controller_acquired = False
        self._last_status: YarboTelemetry | None = None
        self._mqtt_log_path = mqtt_log_path
        self._debug = debug
        self._debug_raw = debug_raw
        self._mqtt_capture_max = mqtt_capture_max
        self._captured_mqtt: list[dict[str, Any]] = []

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

    def get_captured_mqtt(self) -> list[dict[str, Any]]:
        """Return captured MQTT messages (populated when mqtt_capture_max > 0)."""
        return list(self._captured_mqtt)

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

    async def get_controller(self, timeout: float | None = None) -> YarboCommandResult:
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
        except BaseException:
            # publish() failed — wait_for_message's finally block never runs, so
            # we must release the pre-registered queue here to prevent a leak.
            self._transport.release_queue(wait_queue)
            raise
        msg = await self._transport.wait_for_message(
            timeout=timeout or DEFAULT_CMD_TIMEOUT,
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
            result = YarboTelemetry.from_dict(payload, topic=topic)
            self._last_status = result
            return result
        return None

    def _validate_head_type(self, required: HeadType | tuple[HeadType, ...]) -> None:
        """Validate that the attached head matches the required type(s).

        Args:
            required: A single :class:`~yarbo.models.HeadType` or a tuple of
                      acceptable head types.

        Raises:
            ValueError: If a head status has been received and the attached head
                        does not match *required*.

        If no status has been received yet, a warning is logged and the command
        is allowed through.
        """
        if isinstance(required, HeadType):
            required = (required,)

        if self._last_status is None or self._last_status.head_type is None:
            logger.warning(
                "Head type unknown (no status received yet) — allowing command; expected one of %s",
                [h.name for h in required],
            )
            return

        try:
            current = HeadType(self._last_status.head_type)
        except ValueError:
            logger.warning(
                "Unknown head_type value %r — allowing command",
                self._last_status.head_type,
            )
            return

        if current not in required:
            req_names = " or ".join(h.name for h in required)
            raise ValueError(f"Command requires {req_names} head, but {current.name} is attached")

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
        except BaseException:
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
        except BaseException:
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
        except BaseException:
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

    async def delete_plan(self, plan_id: str, *, confirm: bool = False) -> YarboCommandResult:
        """Delete a plan by its ID.

        Args:
            plan_id: UUID of the plan to delete.
            confirm: Must be ``True`` to confirm this destructive operation.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            ValueError:        If *confirm* is not ``True``.
            YarboTimeoutError: If no acknowledgement is received.
        """
        if not confirm:
            raise ValueError(
                "delete_plan is a destructive operation. Pass confirm=True to confirm."
            )
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
        except BaseException:
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
        return dict(msg.get("data", {}) or {})

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
        except BaseException:
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
        return dict(msg.get("data", {}) or {})

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
    # Robot control
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Power off the robot."""
        await self._ensure_controller()
        await self._transport.publish("shutdown", {})

    async def restart_container(self) -> None:
        """Restart the EMQX container on the robot."""
        await self._ensure_controller()
        await self._transport.publish("restart_container", {})

    async def emergency_stop(self) -> None:
        """Trigger an emergency stop."""
        await self._ensure_controller()
        await self._transport.publish("emergency_stop_active", {})

    async def emergency_unlock(self) -> None:
        """Clear the emergency stop state."""
        await self._ensure_controller()
        await self._transport.publish("emergency_unlock", {})

    async def dstop(self) -> None:
        """Soft-stop the robot (decelerate to halt)."""
        await self._ensure_controller()
        await self._transport.publish("dstop", {})

    async def resume(self) -> None:
        """Resume operation after a pause or soft-stop."""
        await self._ensure_controller()
        await self._transport.publish("resume", {})

    async def cmd_recharge(self) -> None:
        """Send the robot back to its charging dock."""
        await self._ensure_controller()
        await self._transport.publish("cmd_recharge", {})

    # ------------------------------------------------------------------
    # Lights & sound
    # ------------------------------------------------------------------

    async def set_head_light(self, enabled: bool) -> None:
        """
        Enable or disable the head light.

        Args:
            enabled: True to turn on, False to turn off.
        """
        await self._ensure_controller()
        await self._transport.publish("head_light", {"state": 1 if enabled else 0})

    async def set_roof_lights(self, enabled: bool) -> None:
        """
        Enable or disable the roof lights.

        Args:
            enabled: True to turn on, False to turn off.
        """
        await self._ensure_controller()
        await self._transport.publish("roof_lights_enable", {"enable": 1 if enabled else 0})

    async def set_laser(self, enabled: bool) -> None:
        """
        Enable or disable the laser.

        Args:
            enabled: True to enable, False to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("laser_toggle", {"enabled": enabled})

    async def set_sound(self, volume: int, song_id: int = 0) -> None:
        """
        Set the speaker volume (sound parameter variant A).

        Args:
            volume:  Volume level (0-100).
            song_id: Song identifier (reserved, default 0).
        """
        await self._ensure_controller()
        await self._transport.publish("set_sound_param", {"vol": volume, "songId": song_id})

    async def set_sound_param(self, volume: int, enabled: int) -> None:
        """
        Set the speaker volume and enable/disable audio output (variant B).

        Uses the same ``set_sound_param`` command but with a different payload
        shape than :meth:`set_sound`.  Both variants are known to exist in
        firmware captures.

        Args:
            volume:  Volume level (0-100).
            enabled: 1 to enable audio output, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("set_sound_param", {"volume": volume, "enable": enabled})

    async def play_song(self, song_id: int) -> None:
        """
        Play a sound/song by ID.

        Args:
            song_id: Identifier of the song to play.
        """
        await self._ensure_controller()
        await self._transport.publish("song_cmd", {"songId": song_id})

    # ------------------------------------------------------------------
    # Camera & detection
    # ------------------------------------------------------------------

    async def set_camera(self, enabled: bool) -> None:
        """
        Enable or disable the camera.

        Args:
            enabled: True to enable, False to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("camera_toggle", {"enabled": enabled})

    async def set_person_detect(self, enabled: bool) -> None:
        """
        Enable or disable person detection.

        Args:
            enabled: True to enable, False to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("set_person_detect", {"enable": 1 if enabled else 0})

    async def set_usb(self, enabled: bool) -> None:
        """
        Enable or disable the USB port.

        Args:
            enabled: True to enable, False to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("usb_toggle", {"enabled": enabled})

    async def check_camera_status(self) -> YarboCommandResult:
        """Request current camera status."""
        return await self._publish_and_wait("check_camera_status", {})

    async def camera_calibration(self) -> YarboCommandResult:
        """Trigger camera calibration."""
        return await self._publish_and_wait("camera_calibration", {})

    # ------------------------------------------------------------------
    # Plans & scheduling
    # ------------------------------------------------------------------

    async def start_plan_direct(self, plan_id: int, percent: int = 100) -> None:
        """
        Start a work plan by numeric ID (direct command, no response).

        Args:
            plan_id: Numeric ID of the plan to execute.
            percent: Coverage percentage (default 100).
        """
        await self._ensure_controller()
        await self._transport.publish("start_plan", {"planId": plan_id, "percent": percent})

    async def read_plan(self, plan_id: int, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request detail for a specific plan and await the data_feedback response.

        Args:
            plan_id: Numeric plan ID.
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("read_plan", {"id": plan_id}, timeout)

    async def read_all_plans(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request all plan summaries and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("read_all_plan", {}, timeout)

    async def delete_plan_direct(self, plan_id: int, confirm: bool = False) -> None:
        """
        Delete a plan by numeric ID (direct command, no response).

        Args:
            plan_id: Numeric plan ID to delete.
            confirm: Must be ``True`` to proceed — this is a destructive operation.

        Raises:
            ValueError: If *confirm* is not ``True``.
        """
        if not confirm:
            raise ValueError("delete_plan_direct is destructive — pass confirm=True to proceed.")
        await self._ensure_controller()
        await self._transport.publish("del_plan", {"planId": plan_id})

    async def delete_all_plans(self, confirm: bool = False) -> None:
        """Delete all stored plans from the robot.

        Args:
            confirm: Must be ``True`` to proceed — this is a destructive operation.

        Raises:
            ValueError: If *confirm* is not ``True``.
        """
        if not confirm:
            raise ValueError("delete_all_plans is destructive — pass confirm=True to proceed.")
        await self._ensure_controller()
        await self._transport.publish("del_all_plan", {})

    async def pause_planning(self) -> None:
        """Pause the currently running plan (direct command, no response)."""
        await self._ensure_controller()
        await self._transport.publish("planning_paused", {})

    async def in_plan_action(self, action: str) -> None:
        """
        Send an in-plan action command.

        Args:
            action: Action string (e.g. ``"pause"``, ``"resume"``, ``"stop"``).
        """
        await self._ensure_controller()
        await self._transport.publish("in_plan_action", {"action": action})

    async def read_schedules(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request all schedules and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("read_schedules", {}, timeout)

    # ------------------------------------------------------------------
    # Navigation & maps
    # ------------------------------------------------------------------

    async def start_waypoint(self, index: int) -> None:
        """
        Start navigation to a waypoint by index.

        Args:
            index: Zero-based waypoint index.
        """
        await self._ensure_controller()
        await self._transport.publish("start_way_point", {"index": index})

    async def read_recharge_point(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request the saved recharge/dock point and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("read_recharge_point", {}, timeout)

    async def save_charging_point(self) -> None:
        """Save the robot's current position as the charging/dock point."""
        await self._ensure_controller()
        await self._transport.publish("save_charging_point", {})

    async def read_clean_area(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request the clean area definition and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("read_clean_area", {}, timeout)

    async def get_all_map_backup(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request all map backups and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("get_all_map_backup", {}, timeout)

    async def save_map_backup(self) -> None:
        """Save a backup of the current map."""
        await self._ensure_controller()
        await self._transport.publish("save_map_backup", {})

    # ------------------------------------------------------------------
    # WiFi & connectivity
    # ------------------------------------------------------------------

    async def get_wifi_list(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request the list of available WiFi networks and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("get_wifi_list", {}, timeout)

    async def get_connected_wifi(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request the currently connected WiFi network name and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("get_connect_wifi_name", {}, timeout)

    async def start_hotspot(self) -> None:
        """Start the robot's WiFi hotspot."""
        await self._ensure_controller()
        await self._transport.publish("start_hotspot", {})

    async def get_hub_info(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request hub information and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("hub_info", {}, timeout)

    async def get_saved_wifi_list(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request saved Wi-Fi networks from the robot and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("get_saved_wifi_list", {}, timeout)

    # ------------------------------------------------------------------
    # Diagnostics (read-only telemetry requests)
    # ------------------------------------------------------------------

    async def read_no_charge_period(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request no-charge period configuration and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("read_no_charge_period", {}, timeout)

    async def get_battery_cell_temps(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request battery cell temperature data and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("battery_cell_temp_msg", {}, timeout)

    async def get_motor_temps(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request motor temperature data and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("motor_temp_samp", {}, timeout)

    async def get_body_current(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request body current telemetry and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("body_current_msg", {}, timeout)

    async def get_head_current(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request head current telemetry and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("head_current_msg", {}, timeout)

    async def get_speed(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request current speed telemetry and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("speed_msg", {}, timeout)

    async def get_odometer(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request odometer data and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("odometer_msg", {}, timeout)

    async def get_product_code(self, timeout: float = 5.0) -> dict[str, Any]:
        """
        Request the product code and await the data_feedback response.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback("product_code_msg", {}, timeout)

    # ------------------------------------------------------------------
    # Data feedback helper
    # ------------------------------------------------------------------

    async def _request_data_feedback(
        self, cmd: str, payload: dict[str, Any], timeout: float = 5.0
    ) -> dict[str, Any]:
        """
        Send a command and wait for the matching ``data_feedback`` response.

        Pre-registers a receive queue before publishing to eliminate any
        publish/subscribe race condition.

        Args:
            cmd:     Topic leaf name of the command to send.
            payload: Payload dict to publish.
            timeout: Seconds to wait for the response.

        Returns:
            Decoded response payload dict, or empty dict on timeout.
        """
        await self._ensure_controller()
        wait_queue = self._transport.create_wait_queue()
        try:
            await self._transport.publish(cmd, payload)
            msg = await self._transport.wait_for_message(
                timeout=timeout,
                feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
                command_name=cmd,
                _queue=wait_queue,
            )
            return msg.get("data", {}) or {} if isinstance(msg, dict) else {}
        except BaseException:
            self._transport.release_queue(wait_queue)
            raise

    # ------------------------------------------------------------------
    # Raw publish (escape hatch)
    # ------------------------------------------------------------------

    async def publish_command(self, cmd: str, payload: dict[str, Any]) -> None:
        """
        Publish a command to the robot without auto-acquiring the controller.

        Used by the coordinator which manages controller acquisition separately
        (calls ``get_controller()`` explicitly before command sequences).

        Topic: ``snowbot/{SN}/app/{cmd}``, payload: zlib-compressed JSON.

        Args:
            cmd:     Topic leaf (e.g. ``"start_plan"``).
            payload: Dict payload (will be zlib-encoded).
        """
        await self._transport.publish(cmd, payload)

    async def publish_raw(self, cmd: str, payload: dict[str, Any]) -> None:
        """
        Publish an arbitrary command to the robot.

        Useful for commands not yet wrapped in a dedicated method.
        Auto-acquires controller role if needed (use :meth:`publish_command`
        to skip auto-acquire, e.g. in coordinator patterns).

        Args:
            cmd:     Topic leaf (e.g. ``"start_plan"``).
            payload: Dict payload (will be zlib-encoded).
        """
        await self._ensure_controller()
        await self._transport.publish(cmd, payload)

    # ------------------------------------------------------------------
    # Blade / mowing configuration
    # ------------------------------------------------------------------

    async def set_blade_height(self, height: int) -> None:
        """Set the blade cutting height.

        Args:
            height: Blade height value (robot-defined units).
        """
        self._validate_head_type((HeadType.LawnMower, HeadType.LawnMowerPro))
        await self._ensure_controller()
        await self._transport.publish("set_blade_height", {"height": height})

    async def set_blade_speed(self, speed: int) -> None:
        """Set the blade rotation speed.

        Args:
            speed: Blade speed value (robot-defined units).
        """
        self._validate_head_type((HeadType.LawnMower, HeadType.LawnMowerPro))
        await self._ensure_controller()
        await self._transport.publish("set_blade_speed", {"speed": speed})

    async def set_charge_limit(self, min_pct: int, max_pct: int) -> None:
        """Set battery charge limits.

        Args:
            min_pct: Minimum charge percentage before robot returns to dock.
            max_pct: Maximum charge percentage (charge stops here).
        """
        await self._ensure_controller()
        await self._transport.publish("set_charge_limit", {"min": min_pct, "max": max_pct})

    async def set_turn_type(self, turn_type: int) -> None:
        """Set the turning behaviour type.

        Args:
            turn_type: Integer representing the turn mode (robot-defined).
        """
        await self._ensure_controller()
        await self._transport.publish("set_turn_type", {"turnType": turn_type})

    # ------------------------------------------------------------------
    # Snow blower accessories
    # ------------------------------------------------------------------

    async def push_snow_dir(self, direction: int) -> None:
        """Set the snow push direction.

        Args:
            direction: Direction integer (robot-defined).
        """
        self._validate_head_type(HeadType.SnowBlower)
        await self._ensure_controller()
        await self._transport.publish("push_snow_dir", {"dir": direction})

    async def set_chute_steering_work(self, state: int) -> None:
        """Set the chute steering state during work.

        Args:
            state: Chute steering state (robot-defined).
        """
        self._validate_head_type(HeadType.SnowBlower)
        await self._ensure_controller()
        await self._transport.publish("set_chute_steering_work", {"state": state})

    async def set_roller_speed(self, speed: int) -> None:
        """Set the roller/blower speed.

        Args:
            speed: Speed value (robot-defined units).
        """
        self._validate_head_type(HeadType.LeafBlower)
        await self._ensure_controller()
        await self._transport.publish("set_roller_speed", {"speed": speed})

    # ------------------------------------------------------------------
    # Motor & mechanical
    # ------------------------------------------------------------------

    async def set_motor_protect(self, state: int) -> None:
        """Enable or disable motor protection mode.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("cmd_motor_protect", {"state": state})

    async def set_trimmer(self, state: int) -> None:
        """Enable or disable the trimmer.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("cmd_trimmer", {"state": state})

    # ------------------------------------------------------------------
    # Blowing / edge / smart features
    # ------------------------------------------------------------------

    async def set_edge_blowing(self, state: int) -> None:
        """Enable or disable edge blowing.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("edge_blowing", {"state": state})

    async def set_smart_blowing(self, state: int) -> None:
        """Enable or disable smart blowing.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("smart_blowing", {"state": state})

    async def set_heating_film(self, state: int) -> None:
        """Enable or disable heating film (anti-ice).

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("heating_film_ctrl", {"state": state})

    async def set_module_lock(self, state: int) -> None:
        """Lock or unlock an accessory module.

        Args:
            state: 1 to lock, 0 to unlock.
        """
        await self._ensure_controller()
        await self._transport.publish("module_lock_ctl", {"state": state})

    # ------------------------------------------------------------------
    # Autonomous modes
    # ------------------------------------------------------------------

    async def set_follow_mode(self, state: int) -> None:
        """Enable or disable follow mode.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("set_follow_state", {"state": state})

    async def set_draw_mode(self, state: int) -> None:
        """Enable or disable draw/mapping mode.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("start_draw_cmd", {"state": state})

    # ------------------------------------------------------------------
    # OTA / firmware updates
    # ------------------------------------------------------------------

    async def set_auto_update(self, state: int) -> None:
        """Enable or disable automatic firmware (Greengrass) updates.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("set_greengrass_auto_update_switch", {"state": state})

    async def set_camera_ota(self, state: int) -> None:
        """Enable or disable IP camera OTA updates.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("set_ipcamera_ota_switch", {"state": state})

    async def firmware_update_now(self, *, confirm: bool = False) -> YarboCommandResult:
        """Trigger an immediate firmware update.

        .. warning::
            This is a **destructive** operation. You must pass ``confirm=True`` to proceed.

        Args:
            confirm: Must be ``True`` to proceed.

        Raises:
            ValueError: If *confirm* is not ``True``.
            YarboTimeoutError: If no acknowledgement is received.
        """
        if not confirm:
            raise ValueError("firmware_update_now is destructive — pass confirm=True to proceed.")
        return await self._publish_and_wait("firmware_update_now", {})

    async def firmware_update_tonight(self) -> YarboCommandResult:
        """Schedule a firmware update for tonight."""
        return await self._publish_and_wait("firmware_update_tonight", {})

    async def firmware_update_later(self) -> YarboCommandResult:
        """Defer a pending firmware update."""
        return await self._publish_and_wait("firmware_update_later", {})

    # ------------------------------------------------------------------
    # Vision / recording
    # ------------------------------------------------------------------

    async def set_smart_vision(self, state: int) -> None:
        """Enable or disable smart vision processing.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("smart_vision_control", {"state": state})

    async def set_video_record(self, state: int) -> None:
        """Enable or disable video recording.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("enable_video_record", {"state": state})

    async def bag_record(self, enabled: bool) -> None:
        """Start or stop bag recording.

        Args:
            enabled: True to start recording, False to stop.
        """
        await self._ensure_controller()
        await self._transport.publish("bag_record", {"state": 1 if enabled else 0})

    # ------------------------------------------------------------------
    # Safety / fencing
    # ------------------------------------------------------------------

    async def set_child_lock(self, state: int) -> None:
        """Enable or disable the child lock.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("child_lock", {"state": state})

    async def set_geo_fence(self, state: int) -> None:
        """Enable or disable geo-fencing.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("enable_geo_fence", {"state": state})

    async def set_elec_fence(self, state: int) -> None:
        """Enable or disable the electric fence.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("enable_elec_fence", {"state": state})

    async def set_ngz_edge(self, state: int) -> None:
        """Enable or disable NGZ (no-go-zone) edge enforcement.

        Args:
            state: 1 to enable, 0 to disable.
        """
        await self._ensure_controller()
        await self._transport.publish("ngz_edge", {"state": state})

    # ------------------------------------------------------------------
    # Manual drive extras
    # ------------------------------------------------------------------

    async def set_velocity_manual(self, linear: float, angular: float) -> None:
        """Send a velocity command in manual drive mode.

        Args:
            linear:  Linear velocity (forward positive).
            angular: Angular velocity (counter-clockwise positive).
        """
        await self._ensure_controller()
        await self._transport.publish("cmd_vel", {"vel": linear, "rev": angular})

    # ------------------------------------------------------------------
    # Map management (destructive)
    # ------------------------------------------------------------------

    async def erase_map(self, confirm: bool = False) -> None:
        """Erase the robot's stored map.

        .. warning::
            This is a **destructive** operation. The map cannot be recovered
            after erasure. You must pass ``confirm=True`` to proceed.

        Args:
            confirm: Must be ``True`` to proceed.

        Raises:
            ValueError: If *confirm* is not ``True``.
        """
        if not confirm:
            raise ValueError("erase_map is destructive — pass confirm=True to proceed.")
        await self._ensure_controller()
        await self._transport.publish("erase_map", {})

    async def map_recovery(self, map_id: str, confirm: bool = False) -> None:
        """Restore a map from a backup by ID.

        .. warning::
            This is a **destructive** operation — it overwrites the current map.
            You must pass ``confirm=True`` to proceed.

        Args:
            map_id:  ID of the map backup to restore.
            confirm: Must be ``True`` to proceed.

        Raises:
            ValueError: If *confirm* is not ``True``.
        """
        if not confirm:
            raise ValueError("map_recovery is destructive — pass confirm=True to proceed.")
        await self._ensure_controller()
        await self._transport.publish("map_recovery", {"mapId": map_id})

    async def save_current_map(self) -> None:
        """Save the robot's current map state."""
        await self._ensure_controller()
        await self._transport.publish("save_current_map", {})

    async def save_map_backup_list(self, timeout: float = 5.0) -> dict[str, Any]:
        """Save map backup and retrieve all map backup names and IDs.

        Args:
            timeout: Seconds to wait for the response (default 5.0).

        Returns:
            Response payload dict, or empty dict on timeout.
        """
        return await self._request_data_feedback(
            "save_map_backup_and_get_all_map_backup_nameandid", {}, timeout
        )

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
