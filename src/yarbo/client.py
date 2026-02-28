"""
yarbo.client — YarboClient: hybrid orchestrator (local preferred, cloud fallback).

``YarboClient`` is the primary entry point for most users. It prefers local
MQTT control (fast, offline) and falls back to the cloud API for features
that require it (robot binding, account management, etc.).

Usage::

    # Async context manager
    async with YarboClient(broker="192.168.1.24", sn="24400102L8HO5227") as client:
        status = await client.get_status()
        await client.lights_on()
        async for telemetry in client.watch_telemetry():
            print(f"Battery: {telemetry.battery}%")

    # Sync wrapper
    client = YarboClient.connect(broker="192.168.1.24", sn="24400102L8HO5227")
    client.lights_on()
    client.disconnect()

    # Auto-discovery
    from yarbo import discover_yarbo
    robots = await discover_yarbo()
    if robots:
        async with YarboClient(broker=robots[0].broker_host, sn=robots[0].sn) as client:
            await client.lights_on()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .cloud import YarboCloudClient
from .const import LOCAL_BROKER_DEFAULT, LOCAL_PORT
from .local import YarboLocalClient, _SyncYarboLocalClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime
    from types import TracebackType

    from .models import (
        YarboCommandResult,
        YarboLightState,
        YarboPlan,
        YarboSchedule,
        YarboTelemetry,
    )

logger = logging.getLogger(__name__)


class YarboClient:
    """
    Hybrid Yarbo client — local MQTT control + cloud REST API.

    This client combines:

    * **Local MQTT control** via :class:`~yarbo.local.YarboLocalClient` —
      direct communication with the robot over the local HaLow EMQX broker.
      This is the primary control path: fast, offline-capable, and confirmed
      working on hardware.

    * **Cloud REST API** via :class:`~yarbo.cloud.YarboCloudClient` —
      robot management, account operations, scheduling, and notifications
      via the Yarbo HTTPS API. Requires ``username`` / ``password``.

    .. note:: **Cloud MQTT is available via** :class:`~yarbo.cloud_mqtt.YarboCloudMqttClient`.
        The Yarbo cloud backend supports a Tencent TDMQ MQTT broker for remote
        control. Use :class:`~yarbo.cloud_mqtt.YarboCloudMqttClient` for
        internet-remote control over TLS — it provides the same API surface as
        :class:`~yarbo.local.YarboLocalClient`.

    For purely local usage (no cloud account) this client is equivalent to
    :class:`~yarbo.local.YarboLocalClient`.

    Args:
        broker:    Local MQTT broker IP address.
        sn:        Robot serial number.
        port:      MQTT broker port (default 1883).
        username:  Cloud account email (optional — for cloud REST features only).
        password:  Cloud account password (optional).
        rsa_key_path: Path to the RSA public key PEM (for cloud auth).
        auto_controller: Send ``get_controller`` automatically (default True).
    """

    def __init__(
        self,
        broker: str = LOCAL_BROKER_DEFAULT,
        sn: str = "",
        port: int = LOCAL_PORT,
        username: str = "",
        password: str = "",
        rsa_key_path: str | None = None,
        auto_controller: bool = True,
    ) -> None:
        self._local = YarboLocalClient(
            broker=broker,
            sn=sn,
            port=port,
            auto_controller=auto_controller,
        )
        self._cloud_username = username
        self._cloud_password = password
        self._cloud_rsa_key_path = rsa_key_path
        self._cloud: YarboCloudClient | None = None  # lazily initialised

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> YarboClient:
        await self._local.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._local.disconnect()
        if self._cloud:
            await self._cloud.disconnect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the local MQTT broker."""
        await self._local.connect()

    async def disconnect(self) -> None:
        """Disconnect from the broker and cloud client."""
        await self._local.disconnect()
        if self._cloud:
            await self._cloud.disconnect()

    @property
    def is_connected(self) -> bool:
        """True if the local MQTT connection is active."""
        return self._local.is_connected

    @property
    def serial_number(self) -> str:
        """Robot serial number (read-only)."""
        return self._local.serial_number

    @property
    def controller_acquired(self) -> bool:
        """True if the controller role has been acquired on the local MQTT broker."""
        return self._local.controller_acquired

    # ------------------------------------------------------------------
    # Local commands (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def get_controller(self) -> YarboCommandResult:
        """
        Acquire controller role. Called automatically before most commands.

        Returns:
            :class:`~yarbo.models.YarboCommandResult` on success.

        Raises:
            YarboNotControllerError: If the robot rejects the handshake.
            YarboTimeoutError: If no acknowledgement is received in time.
        """
        return await self._local.get_controller()

    async def get_status(self, timeout: float = 5.0) -> YarboTelemetry | None:
        """Return a single telemetry snapshot from the robot."""
        return await self._local.get_status(timeout=timeout)

    async def watch_telemetry(self) -> AsyncIterator[YarboTelemetry]:
        """Async generator yielding live telemetry from the robot."""
        async for t in self._local.watch_telemetry():
            yield t

    async def set_lights(self, state: YarboLightState) -> None:
        """Set all 7 LED channels."""
        await self._local.set_lights(state)

    async def lights_on(self) -> None:
        """Turn all lights on at full brightness."""
        await self._local.lights_on()

    async def lights_off(self) -> None:
        """Turn all lights off."""
        await self._local.lights_off()

    async def buzzer(self, state: int = 1) -> None:
        """Trigger the buzzer (state=1 play, state=0 stop)."""
        await self._local.buzzer(state=state)

    async def set_chute(self, vel: int) -> None:
        """Set snow chute direction (snow blower models only)."""
        await self._local.set_chute(vel=vel)

    async def publish_raw(self, cmd: str, payload: dict[str, Any]) -> None:
        """Publish an arbitrary MQTT command to the robot."""
        await self._local.publish_raw(cmd, payload)

    # -- Robot control --

    async def shutdown(self) -> None:
        """Power off the robot."""
        await self._local.shutdown()

    async def restart_container(self) -> None:
        """Restart the EMQX container on the robot."""
        await self._local.restart_container()

    async def emergency_stop(self) -> None:
        """Trigger an emergency stop."""
        await self._local.emergency_stop()

    async def emergency_unlock(self) -> None:
        """Clear the emergency stop state."""
        await self._local.emergency_unlock()

    async def dstop(self) -> None:
        """Soft-stop the robot (decelerate to halt)."""
        await self._local.dstop()

    async def resume(self) -> None:
        """Resume operation after a pause or soft-stop."""
        await self._local.resume()

    async def cmd_recharge(self) -> None:
        """Send the robot back to its charging dock."""
        await self._local.cmd_recharge()

    # -- Lights & sound --

    async def set_head_light(self, enabled: bool) -> None:
        """Enable or disable the head light."""
        await self._local.set_head_light(enabled)

    async def set_roof_lights(self, enabled: bool) -> None:
        """Enable or disable the roof lights."""
        await self._local.set_roof_lights(enabled)

    async def set_laser(self, enabled: bool) -> None:
        """Enable or disable the laser."""
        await self._local.set_laser(enabled)

    async def set_sound(self, volume: int, song_id: int = 0) -> None:
        """Set the speaker volume (0-100)."""
        await self._local.set_sound(volume, song_id)

    async def play_song(self, song_id: int) -> None:
        """Play a sound/song by ID."""
        await self._local.play_song(song_id)

    # -- Camera & detection --

    async def set_camera(self, enabled: bool) -> None:
        """Enable or disable the camera."""
        await self._local.set_camera(enabled)

    async def set_person_detect(self, enabled: bool) -> None:
        """Enable or disable person detection."""
        await self._local.set_person_detect(enabled)

    async def set_usb(self, enabled: bool) -> None:
        """Enable or disable the USB port."""
        await self._local.set_usb(enabled)

    # -- Plans & scheduling --

    async def start_plan(self, plan_id: int, percent: int = 100) -> None:
        """Start a work plan by ID."""
        await self._local.start_plan(plan_id, percent)

    async def read_plan(self, plan_id: int, timeout: float = 5.0) -> dict[str, Any]:
        """Request plan detail and await the data_feedback response."""
        return await self._local.read_plan(plan_id, timeout)

    async def read_all_plans(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request all plan summaries and await the data_feedback response."""
        return await self._local.read_all_plans(timeout)

    async def delete_plan(self, plan_id: int) -> None:
        """Delete a plan by ID."""
        await self._local.delete_plan(plan_id)

    async def delete_all_plans(self) -> None:
        """Delete all stored plans from the robot."""
        await self._local.delete_all_plans()

    async def pause_plan(self) -> None:
        """Pause the currently running plan."""
        await self._local.pause_plan()

    async def in_plan_action(self, action: str) -> None:
        """Send an in-plan action command."""
        await self._local.in_plan_action(action)

    async def read_schedules(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request all schedules and await the data_feedback response."""
        return await self._local.read_schedules(timeout)

    # -- Navigation & maps --

    async def start_waypoint(self, index: int) -> None:
        """Start navigation to a waypoint by index."""
        await self._local.start_waypoint(index)

    async def read_recharge_point(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request the saved recharge/dock point and await the data_feedback response."""
        return await self._local.read_recharge_point(timeout)

    async def save_charging_point(self) -> None:
        """Save the robot's current position as the charging/dock point."""
        await self._local.save_charging_point()

    async def read_clean_area(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request the clean area definition and await the data_feedback response."""
        return await self._local.read_clean_area(timeout)

    async def get_all_map_backup(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request all map backups and await the data_feedback response."""
        return await self._local.get_all_map_backup(timeout)

    async def save_map_backup(self) -> None:
        """Save a backup of the current map."""
        await self._local.save_map_backup()

    # -- WiFi & connectivity --

    async def get_wifi_list(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request the list of available WiFi networks and await the data_feedback response."""
        return await self._local.get_wifi_list(timeout)

    async def get_connected_wifi(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request the connected WiFi network name and await the data_feedback response."""
        return await self._local.get_connected_wifi(timeout)

    async def start_hotspot(self) -> None:
        """Start the robot's WiFi hotspot."""
        await self._local.start_hotspot()

    async def get_hub_info(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request hub information and await the data_feedback response."""
        return await self._local.get_hub_info(timeout)

    # -- Diagnostics --

    async def read_no_charge_period(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request no-charge period configuration and await the data_feedback response."""
        return await self._local.read_no_charge_period(timeout)

    async def get_battery_cell_temps(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request battery cell temperature data and await the data_feedback response."""
        return await self._local.get_battery_cell_temps(timeout)

    async def get_motor_temps(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request motor temperature data and await the data_feedback response."""
        return await self._local.get_motor_temps(timeout)

    async def get_body_current(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request body current telemetry and await the data_feedback response."""
        return await self._local.get_body_current(timeout)

    async def get_head_current(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request head current telemetry and await the data_feedback response."""
        return await self._local.get_head_current(timeout)

    async def get_speed(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request current speed telemetry and await the data_feedback response."""
        return await self._local.get_speed(timeout)

    async def get_odometer(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request odometer data and await the data_feedback response."""
        return await self._local.get_odometer(timeout)

    async def get_product_code(self, timeout: float = 5.0) -> dict[str, Any]:
        """Request the product code and await the data_feedback response."""
        return await self._local.get_product_code(timeout)

    # ------------------------------------------------------------------
    # Plan management (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def start_plan(self, plan_id: str) -> YarboCommandResult:
        """Start the plan identified by *plan_id*."""
        return await self._local.start_plan(plan_id)

    async def stop_plan(self) -> YarboCommandResult:
        """Stop the currently running plan."""
        return await self._local.stop_plan()

    async def pause_plan(self) -> YarboCommandResult:
        """Pause the currently running plan."""
        return await self._local.pause_plan()

    async def resume_plan(self) -> YarboCommandResult:
        """Resume a paused plan."""
        return await self._local.resume_plan()

    async def return_to_dock(self) -> YarboCommandResult:
        """Send the robot back to its charging dock."""
        return await self._local.return_to_dock()

    # ------------------------------------------------------------------
    # Schedule management (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def list_schedules(self, timeout: float = 5.0) -> list[YarboSchedule]:
        """Fetch the list of saved schedules from the robot."""
        return await self._local.list_schedules(timeout=timeout)

    async def set_schedule(self, schedule: YarboSchedule) -> YarboCommandResult:
        """Save or update a schedule on the robot."""
        return await self._local.set_schedule(schedule)

    async def delete_schedule(self, schedule_id: str) -> YarboCommandResult:
        """Delete a schedule by its ID."""
        return await self._local.delete_schedule(schedule_id)

    # ------------------------------------------------------------------
    # Plan CRUD (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def list_plans(self, timeout: float = 5.0) -> list[YarboPlan]:
        """Fetch the list of saved plans from the robot."""
        return await self._local.list_plans(timeout=timeout)

    async def delete_plan(self, plan_id: str) -> YarboCommandResult:
        """Delete a plan by its ID."""
        return await self._local.delete_plan(plan_id)

    async def create_plan(
        self,
        name: str,
        area_ids: list[int],
        enable_self_order: bool = False,
    ) -> YarboCommandResult:
        """Create a new work plan on the robot."""
        return await self._local.create_plan(
            name=name, area_ids=area_ids, enable_self_order=enable_self_order
        )

    # ------------------------------------------------------------------
    # Manual drive (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def start_manual_drive(self) -> None:
        """Enter manual drive mode."""
        await self._local.start_manual_drive()

    async def set_velocity(self, linear: float, angular: float = 0.0) -> None:
        """Send a velocity command (linear m/s, angular rad/s)."""
        await self._local.set_velocity(linear=linear, angular=angular)

    async def set_roller(self, speed: int) -> None:
        """Set roller speed in RPM (0-2000)."""
        await self._local.set_roller(speed=speed)

    async def stop_manual_drive(
        self, hard: bool = False, emergency: bool = False
    ) -> YarboCommandResult:
        """Exit manual drive mode and stop the robot."""
        return await self._local.stop_manual_drive(hard=hard, emergency=emergency)

    # ------------------------------------------------------------------
    # Global params (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def get_global_params(self, timeout: float = 5.0) -> dict[str, Any]:
        """Fetch all global robot parameters."""
        return await self._local.get_global_params(timeout=timeout)

    async def set_global_params(self, params: dict[str, Any]) -> YarboCommandResult:
        """Save global robot parameters."""
        return await self._local.set_global_params(params=params)

    # ------------------------------------------------------------------
    # Map retrieval (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    async def get_map(self, timeout: float = 10.0) -> dict[str, Any]:
        """Retrieve the robot's current map data."""
        return await self._local.get_map(timeout=timeout)

    # ------------------------------------------------------------------
    # Connection health (delegated to YarboLocalClient)
    # ------------------------------------------------------------------

    @property
    def last_heartbeat(self) -> datetime | None:
        """UTC datetime of the last received heartbeat, or ``None``."""
        return self._local.last_heartbeat

    def is_healthy(self, max_age_seconds: float = 60.0) -> bool:
        """Return ``True`` if a heartbeat was received within *max_age_seconds*."""
        return self._local.is_healthy(max_age_seconds=max_age_seconds)

    # ------------------------------------------------------------------
    # Cloud features (lazy-initialised)
    # ------------------------------------------------------------------

    async def _get_cloud(self) -> YarboCloudClient:
        """Lazily initialise the cloud client."""
        if self._cloud is None:
            self._cloud = YarboCloudClient(
                username=self._cloud_username,
                password=self._cloud_password,
                rsa_key_path=self._cloud_rsa_key_path,
            )
            await self._cloud.connect()
        return self._cloud

    async def list_robots(self) -> list[Any]:
        """
        List all robots bound to the cloud account.

        Requires ``username`` and ``password`` to be provided at construction.
        """
        cloud = await self._get_cloud()
        return await cloud.list_robots()

    async def get_latest_version(self) -> dict[str, Any]:
        """Get the latest app, firmware, and dock-controller versions from the cloud."""
        cloud = await self._get_cloud()
        return await cloud.get_latest_version()

    # ------------------------------------------------------------------
    # Sync factory
    # ------------------------------------------------------------------

    @classmethod
    def connect_sync(
        cls,
        broker: str = LOCAL_BROKER_DEFAULT,
        sn: str = "",
        port: int = LOCAL_PORT,
    ) -> _SyncYarboLocalClient:
        """
        Create a synchronous (blocking) client.

        This is a convenience factory for scripts and interactive sessions.
        Returns a :class:`~yarbo.local._SyncYarboLocalClient` wrapper.

        Example::

            client = YarboClient.connect_sync(broker="192.168.1.24", sn="24400102...")
            client.lights_on()
            client.disconnect()
        """
        return _SyncYarboLocalClient(broker=broker, sn=sn, port=port)
