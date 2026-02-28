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
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_DEVICE_MSG,
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

    async def connect(self) -> None:
        """Connect to the local MQTT broker."""
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

    # ------------------------------------------------------------------
    # Controller handshake
    # ------------------------------------------------------------------

    async def get_controller(self) -> YarboCommandResult | None:
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
        await self._transport.publish("get_controller", {})
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
        raise YarboTimeoutError(
            "Timed out waiting for get_controller acknowledgement from robot."
        )

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
            return YarboTelemetry.from_dict(msg)
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
        async for envelope in self._transport.telemetry_stream():
            if envelope.is_telemetry:
                yield envelope.to_telemetry()

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
        Set the speaker volume.

        Args:
            volume:  Volume level (0-100).
            song_id: Song identifier (reserved, default 0).
        """
        await self._ensure_controller()
        await self._transport.publish("set_sound_param", {"vol": volume})

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

    # ------------------------------------------------------------------
    # Plans & scheduling
    # ------------------------------------------------------------------

    async def start_plan(self, plan_id: int, percent: int = 100) -> None:
        """
        Start a work plan by ID.

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

    async def delete_plan(self, plan_id: int) -> None:
        """
        Delete a plan by ID.

        Args:
            plan_id: Numeric plan ID to delete.
        """
        await self._ensure_controller()
        await self._transport.publish("del_plan", {"planId": plan_id})

    async def delete_all_plans(self) -> None:
        """Delete all stored plans from the robot."""
        await self._ensure_controller()
        await self._transport.publish("del_all_plan", {})

    async def pause_plan(self) -> None:
        """Pause the currently running plan."""
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
        await self._transport.publish(cmd, payload)
        msg = await self._transport.wait_for_message(
            timeout=timeout,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
            command_name=cmd,
            _queue=wait_queue,
        )
        return msg if isinstance(msg, dict) else {}

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
