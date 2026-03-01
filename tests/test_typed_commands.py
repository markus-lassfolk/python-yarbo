"""Tests for the typed command methods added to YarboLocalClient and YarboClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo.client import YarboClient
from yarbo.local import YarboLocalClient

# ---------------------------------------------------------------------------
# Shared fixture: mock MqttTransport for YarboLocalClient tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport():
    """Mock MqttTransport so no real MQTT broker is required."""
    with patch("yarbo.local.MqttTransport") as MockTransport:  # noqa: N806
        instance = MagicMock()
        instance.connect = AsyncMock()
        instance.disconnect = AsyncMock()
        instance.publish = AsyncMock()
        instance.wait_for_message = AsyncMock(return_value={"topic": "cmd", "data": {}})
        instance.create_wait_queue = MagicMock(return_value=MagicMock())
        instance.is_connected = True
        MockTransport.return_value = instance
        yield instance


@pytest.fixture
def client(mock_transport):
    """A connected YarboLocalClient with controller already acquired."""
    c = YarboLocalClient(broker="192.168.1.24", sn="TEST")
    c._controller_acquired = True
    return c


# ---------------------------------------------------------------------------
# Shared fixture: mock YarboLocalClient for YarboClient delegation tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_local():
    """Replace YarboLocalClient with a mock inside YarboClient."""
    with patch("yarbo.client.YarboLocalClient") as MockLocal:  # noqa: N806
        instance = MagicMock()
        instance.connect = AsyncMock()
        instance.disconnect = AsyncMock()
        instance.is_connected = True

        # All new typed methods
        for name in [
            "shutdown",
            "restart_container",
            "emergency_stop",
            "emergency_unlock",
            "dstop",
            "resume",
            "cmd_recharge",
            "set_head_light",
            "set_roof_lights",
            "set_laser",
            "set_sound",
            "play_song",
            "set_camera",
            "set_person_detect",
            "set_usb",
            "start_plan_direct",
            "delete_plan_direct",
            "delete_all_plans",
            "pause_planning",
            "in_plan_action",
            "start_waypoint",
            "save_charging_point",
            "save_map_backup",
            "start_hotspot",
            # new typed methods
            "publish_command",
            "set_blade_height",
            "set_blade_speed",
            "set_charge_limit",
            "set_turn_type",
            "push_snow_dir",
            "set_chute_steering_work",
            "set_roller_speed",
            "set_motor_protect",
            "set_trimmer",
            "set_edge_blowing",
            "set_smart_blowing",
            "set_heating_film",
            "set_module_lock",
            "set_follow_mode",
            "set_draw_mode",
            "set_auto_update",
            "set_camera_ota",
            "set_smart_vision",
            "set_video_record",
            "set_child_lock",
            "set_geo_fence",
            "set_elec_fence",
            "set_ngz_edge",
            "set_velocity_manual",
            "set_sound_param",
            "erase_map",
            "map_recovery",
            "save_current_map",
            # data_feedback methods return a dict
            "read_plan",
            "read_all_plans",
            "read_schedules",
            "read_recharge_point",
            "read_clean_area",
            "get_all_map_backup",
            "get_wifi_list",
            "get_connected_wifi",
            "get_hub_info",
            "read_no_charge_period",
            "get_battery_cell_temps",
            "get_motor_temps",
            "get_body_current",
            "get_head_current",
            "get_speed",
            "get_odometer",
            "get_product_code",
        ]:
            setattr(instance, name, AsyncMock(return_value={}))

        MockLocal.return_value = instance
        yield instance


# ===========================================================================
# Tests: YarboLocalClient — robot control
# ===========================================================================


@pytest.mark.asyncio
class TestRobotControl:
    async def test_shutdown(self, client, mock_transport):
        await client.shutdown()
        mock_transport.publish.assert_called_once_with("shutdown", {})

    async def test_restart_container(self, client, mock_transport):
        await client.restart_container()
        mock_transport.publish.assert_called_once_with("restart_container", {})

    async def test_emergency_stop(self, client, mock_transport):
        await client.emergency_stop()
        mock_transport.publish.assert_called_once_with("emergency_stop_active", {})

    async def test_emergency_unlock(self, client, mock_transport):
        await client.emergency_unlock()
        mock_transport.publish.assert_called_once_with("emergency_unlock", {})

    async def test_dstop(self, client, mock_transport):
        await client.dstop()
        mock_transport.publish.assert_called_once_with("dstop", {})

    async def test_resume(self, client, mock_transport):
        await client.resume()
        mock_transport.publish.assert_called_once_with("resume", {})

    async def test_cmd_recharge(self, client, mock_transport):
        await client.cmd_recharge()
        mock_transport.publish.assert_called_once_with("cmd_recharge", {})


# ===========================================================================
# Tests: YarboLocalClient — lights & sound
# ===========================================================================


@pytest.mark.asyncio
class TestLightsSound:
    async def test_set_head_light_on(self, client, mock_transport):
        await client.set_head_light(True)
        mock_transport.publish.assert_called_once_with("head_light", {"state": 1})

    async def test_set_head_light_off(self, client, mock_transport):
        await client.set_head_light(False)
        mock_transport.publish.assert_called_once_with("head_light", {"state": 0})

    async def test_set_roof_lights_on(self, client, mock_transport):
        await client.set_roof_lights(True)
        mock_transport.publish.assert_called_once_with("roof_lights_enable", {"enable": 1})

    async def test_set_roof_lights_off(self, client, mock_transport):
        await client.set_roof_lights(False)
        mock_transport.publish.assert_called_once_with("roof_lights_enable", {"enable": 0})

    async def test_set_laser_on(self, client, mock_transport):
        await client.set_laser(True)
        mock_transport.publish.assert_called_once_with("laser_toggle", {"enabled": True})

    async def test_set_laser_off(self, client, mock_transport):
        await client.set_laser(False)
        mock_transport.publish.assert_called_once_with("laser_toggle", {"enabled": False})

    async def test_set_sound_volume(self, client, mock_transport):
        await client.set_sound(75)
        mock_transport.publish.assert_called_once_with("set_sound_param", {"vol": 75, "songId": 0})

    async def test_play_song(self, client, mock_transport):
        await client.play_song(3)
        mock_transport.publish.assert_called_once_with("song_cmd", {"songId": 3})


# ===========================================================================
# Tests: YarboLocalClient — camera & detection
# ===========================================================================


@pytest.mark.asyncio
class TestCameraDetection:
    async def test_set_camera_on(self, client, mock_transport):
        await client.set_camera(True)
        mock_transport.publish.assert_called_once_with("camera_toggle", {"enabled": True})

    async def test_set_camera_off(self, client, mock_transport):
        await client.set_camera(False)
        mock_transport.publish.assert_called_once_with("camera_toggle", {"enabled": False})

    async def test_set_person_detect_on(self, client, mock_transport):
        await client.set_person_detect(True)
        mock_transport.publish.assert_called_once_with("set_person_detect", {"enable": 1})

    async def test_set_person_detect_off(self, client, mock_transport):
        await client.set_person_detect(False)
        mock_transport.publish.assert_called_once_with("set_person_detect", {"enable": 0})

    async def test_set_usb_on(self, client, mock_transport):
        await client.set_usb(True)
        mock_transport.publish.assert_called_once_with("usb_toggle", {"enabled": True})

    async def test_set_usb_off(self, client, mock_transport):
        await client.set_usb(False)
        mock_transport.publish.assert_called_once_with("usb_toggle", {"enabled": False})


# ===========================================================================
# Tests: YarboLocalClient — plans & scheduling
# ===========================================================================


@pytest.mark.asyncio
class TestPlansScheduling:
    async def test_start_plan_default_percent(self, client, mock_transport):
        await client.start_plan_direct(7)
        mock_transport.publish.assert_called_once_with("start_plan", {"planId": 7, "percent": 100})

    async def test_start_plan_custom_percent(self, client, mock_transport):
        await client.start_plan_direct(3, percent=50)
        mock_transport.publish.assert_called_once_with("start_plan", {"planId": 3, "percent": 50})

    async def test_read_plan(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "read_plan", "data": {"id": 1}}
        )
        result = await client.read_plan(1)
        mock_transport.publish.assert_called_once_with("read_plan", {"id": 1})
        assert isinstance(result, dict)

    async def test_read_all_plans(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "read_all_plan", "data": []}
        )
        result = await client.read_all_plans()
        mock_transport.publish.assert_called_once_with("read_all_plan", {})
        assert isinstance(result, dict)

    async def test_delete_plan(self, client, mock_transport):
        await client.delete_plan_direct(5, confirm=True)
        mock_transport.publish.assert_called_once_with("del_plan", {"planId": 5})

    async def test_delete_plan_requires_confirm(self, client, mock_transport):
        with pytest.raises(ValueError, match="confirm=True"):
            await client.delete_plan_direct(5)

    async def test_delete_all_plans(self, client, mock_transport):
        await client.delete_all_plans(confirm=True)
        mock_transport.publish.assert_called_once_with("del_all_plan", {})

    async def test_delete_all_plans_requires_confirm(self, client, mock_transport):
        with pytest.raises(ValueError, match="confirm=True"):
            await client.delete_all_plans()

    async def test_pause_plan(self, client, mock_transport):
        await client.pause_planning()
        mock_transport.publish.assert_called_once_with("planning_paused", {})

    async def test_in_plan_action(self, client, mock_transport):
        await client.in_plan_action("pause")
        mock_transport.publish.assert_called_once_with("in_plan_action", {"action": "pause"})

    async def test_read_schedules(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "read_schedules", "data": []}
        )
        result = await client.read_schedules()
        mock_transport.publish.assert_called_once_with("read_schedules", {})
        assert isinstance(result, dict)


# ===========================================================================
# Tests: YarboLocalClient — navigation & maps
# ===========================================================================


@pytest.mark.asyncio
class TestNavigationMaps:
    async def test_start_waypoint(self, client, mock_transport):
        await client.start_waypoint(2)
        mock_transport.publish.assert_called_once_with("start_way_point", {"index": 2})

    async def test_read_recharge_point(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "read_recharge_point"})
        result = await client.read_recharge_point()
        mock_transport.publish.assert_called_once_with("read_recharge_point", {})
        assert isinstance(result, dict)

    async def test_save_charging_point(self, client, mock_transport):
        await client.save_charging_point()
        mock_transport.publish.assert_called_once_with("save_charging_point", {})

    async def test_read_clean_area(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "read_clean_area"})
        result = await client.read_clean_area()
        mock_transport.publish.assert_called_once_with("read_clean_area", {})
        assert isinstance(result, dict)

    async def test_get_all_map_backup(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "get_all_map_backup"})
        result = await client.get_all_map_backup()
        mock_transport.publish.assert_called_once_with("get_all_map_backup", {})
        assert isinstance(result, dict)

    async def test_save_map_backup(self, client, mock_transport):
        await client.save_map_backup()
        mock_transport.publish.assert_called_once_with("save_map_backup", {})


# ===========================================================================
# Tests: YarboLocalClient — WiFi & connectivity
# ===========================================================================


@pytest.mark.asyncio
class TestWifiConnectivity:
    async def test_get_wifi_list(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "get_wifi_list"})
        result = await client.get_wifi_list()
        mock_transport.publish.assert_called_once_with("get_wifi_list", {})
        assert isinstance(result, dict)

    async def test_get_connected_wifi(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "get_connect_wifi_name"})
        result = await client.get_connected_wifi()
        mock_transport.publish.assert_called_once_with("get_connect_wifi_name", {})
        assert isinstance(result, dict)

    async def test_start_hotspot(self, client, mock_transport):
        await client.start_hotspot()
        mock_transport.publish.assert_called_once_with("start_hotspot", {})

    async def test_get_hub_info(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "hub_info"})
        result = await client.get_hub_info()
        mock_transport.publish.assert_called_once_with("hub_info", {})
        assert isinstance(result, dict)


# ===========================================================================
# Tests: YarboLocalClient — diagnostics
# ===========================================================================


@pytest.mark.asyncio
class TestDiagnostics:
    async def test_read_no_charge_period(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "read_no_charge_period"})
        result = await client.read_no_charge_period()
        mock_transport.publish.assert_called_once_with("read_no_charge_period", {})
        assert isinstance(result, dict)

    async def test_get_battery_cell_temps(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "battery_cell_temp_msg"})
        result = await client.get_battery_cell_temps()
        mock_transport.publish.assert_called_once_with("battery_cell_temp_msg", {})
        assert isinstance(result, dict)

    async def test_get_motor_temps(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "motor_temp_samp"})
        result = await client.get_motor_temps()
        mock_transport.publish.assert_called_once_with("motor_temp_samp", {})
        assert isinstance(result, dict)

    async def test_get_body_current(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "body_current_msg"})
        result = await client.get_body_current()
        mock_transport.publish.assert_called_once_with("body_current_msg", {})
        assert isinstance(result, dict)

    async def test_get_head_current(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "head_current_msg"})
        result = await client.get_head_current()
        mock_transport.publish.assert_called_once_with("head_current_msg", {})
        assert isinstance(result, dict)

    async def test_get_speed(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "speed_msg"})
        result = await client.get_speed()
        mock_transport.publish.assert_called_once_with("speed_msg", {})
        assert isinstance(result, dict)

    async def test_get_odometer(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "odometer_msg"})
        result = await client.get_odometer()
        mock_transport.publish.assert_called_once_with("odometer_msg", {})
        assert isinstance(result, dict)

    async def test_get_product_code(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "product_code_msg"})
        result = await client.get_product_code()
        mock_transport.publish.assert_called_once_with("product_code_msg", {})
        assert isinstance(result, dict)


# ===========================================================================
# Tests: _request_data_feedback timeout returns empty dict
# ===========================================================================


@pytest.mark.asyncio
class TestDataFeedbackTimeout:
    async def test_timeout_returns_empty_dict(self, client, mock_transport):
        """On timeout (None from wait_for_message), returns {}."""
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        result = await client.read_plan(1)
        assert result == {}

    async def test_non_dict_response_returns_empty_dict(self, client, mock_transport):
        """On unexpected response type, returns {}."""
        mock_transport.wait_for_message = AsyncMock(return_value="unexpected")
        result = await client.get_speed()
        assert result == {}


# ===========================================================================
# Tests: YarboLocalClient — new typed commands (Task B)
# ===========================================================================


@pytest.mark.asyncio
class TestBladeConfiguration:
    async def test_set_blade_height(self, client, mock_transport):
        await client.set_blade_height(3)
        mock_transport.publish.assert_called_once_with("set_blade_height", {"height": 3})

    async def test_set_blade_speed(self, client, mock_transport):
        await client.set_blade_speed(5)
        mock_transport.publish.assert_called_once_with("set_blade_speed", {"speed": 5})

    async def test_set_charge_limit(self, client, mock_transport):
        await client.set_charge_limit(20, 80)
        mock_transport.publish.assert_called_once_with("set_charge_limit", {"min": 20, "max": 80})

    async def test_set_turn_type(self, client, mock_transport):
        await client.set_turn_type(2)
        mock_transport.publish.assert_called_once_with("set_turn_type", {"turnType": 2})


@pytest.mark.asyncio
class TestSnowBlowerAccessories:
    async def test_push_snow_dir(self, client, mock_transport):
        await client.push_snow_dir(1)
        mock_transport.publish.assert_called_once_with("push_snow_dir", {"direction": 1})

    async def test_set_chute_steering_work(self, client, mock_transport):
        await client.set_chute_steering_work(45)
        mock_transport.publish.assert_called_once_with("cmd_chute_streeing_work", {"angle": 45})

    async def test_set_roller_speed(self, client, mock_transport):
        await client.set_roller_speed(1500)
        mock_transport.publish.assert_called_once_with("cmd_roller", {"speed": 1500})


@pytest.mark.asyncio
class TestMotorAndMechanical:
    async def test_set_motor_protect(self, client, mock_transport):
        await client.set_motor_protect(1)
        mock_transport.publish.assert_called_once_with("cmd_motor_protect", {"state": 1})

    async def test_set_trimmer(self, client, mock_transport):
        await client.set_trimmer(1)
        mock_transport.publish.assert_called_once_with("cmd_trimmer", {"state": 1})


@pytest.mark.asyncio
class TestBlowingAndEdgeFeatures:
    async def test_set_edge_blowing(self, client, mock_transport):
        await client.set_edge_blowing(1)
        mock_transport.publish.assert_called_once_with("edge_blowing", {"state": 1})

    async def test_set_smart_blowing(self, client, mock_transport):
        await client.set_smart_blowing(0)
        mock_transport.publish.assert_called_once_with("smart_blowing", {"state": 0})

    async def test_set_heating_film(self, client, mock_transport):
        await client.set_heating_film(1)
        mock_transport.publish.assert_called_once_with("heating_film_ctrl", {"state": 1})

    async def test_set_module_lock(self, client, mock_transport):
        await client.set_module_lock(1)
        mock_transport.publish.assert_called_once_with("module_lock_ctl", {"state": 1})


@pytest.mark.asyncio
class TestAutonomousModes:
    async def test_set_follow_mode(self, client, mock_transport):
        await client.set_follow_mode(1)
        mock_transport.publish.assert_called_once_with("set_follow_state", {"state": 1})

    async def test_set_draw_mode(self, client, mock_transport):
        await client.set_draw_mode(1)
        mock_transport.publish.assert_called_once_with("start_draw_cmd", {"state": 1})


@pytest.mark.asyncio
class TestOtaUpdates:
    async def test_set_auto_update(self, client, mock_transport):
        await client.set_auto_update(1)
        mock_transport.publish.assert_called_once_with(
            "set_greengrass_auto_update_switch", {"state": 1}
        )

    async def test_set_camera_ota(self, client, mock_transport):
        await client.set_camera_ota(0)
        mock_transport.publish.assert_called_once_with("set_ipcamera_ota_switch", {"state": 0})


@pytest.mark.asyncio
class TestVisionAndRecording:
    async def test_set_smart_vision(self, client, mock_transport):
        await client.set_smart_vision(1)
        mock_transport.publish.assert_called_once_with("smart_vision_control", {"state": 1})

    async def test_set_video_record(self, client, mock_transport):
        await client.set_video_record(1)
        mock_transport.publish.assert_called_once_with("enable_video_record", {"state": 1})


@pytest.mark.asyncio
class TestSafetyAndFencing:
    async def test_set_child_lock(self, client, mock_transport):
        await client.set_child_lock(1)
        mock_transport.publish.assert_called_once_with("child_lock", {"state": 1})

    async def test_set_geo_fence(self, client, mock_transport):
        await client.set_geo_fence(1)
        mock_transport.publish.assert_called_once_with("enable_geo_fence", {"state": 1})

    async def test_set_elec_fence(self, client, mock_transport):
        await client.set_elec_fence(1)
        mock_transport.publish.assert_called_once_with("enable_elec_fence", {"state": 1})

    async def test_set_ngz_edge(self, client, mock_transport):
        await client.set_ngz_edge(0)
        mock_transport.publish.assert_called_once_with("ngz_edge", {"state": 0})


@pytest.mark.asyncio
class TestManualDriveExtras:
    async def test_set_velocity_manual(self, client, mock_transport):
        await client.set_velocity_manual(0.5, -0.3)
        mock_transport.publish.assert_called_once_with("cmd_vel", {"vel": 0.5, "rev": -0.3})

    async def test_set_sound_param(self, client, mock_transport):
        await client.set_sound_param(80, 1)
        mock_transport.publish.assert_called_once_with(
            "set_sound_param", {"volume": 80, "enable": 1}
        )


@pytest.mark.asyncio
class TestPublishCommand:
    async def test_publish_command_delegates_to_transport(self, client, mock_transport):
        await client.publish_command("custom_cmd", {"key": "val"})
        mock_transport.publish.assert_called_once_with("custom_cmd", {"key": "val"})


@pytest.mark.asyncio
class TestDestructiveMapCommands:
    async def test_erase_map_requires_confirm(self, client, mock_transport):
        with pytest.raises(ValueError, match="confirm=True"):
            await client.erase_map()

    async def test_erase_map_with_confirm(self, client, mock_transport):
        await client.erase_map(confirm=True)
        mock_transport.publish.assert_called_once_with("erase_map", {})

    async def test_map_recovery_requires_confirm(self, client, mock_transport):
        with pytest.raises(ValueError, match="confirm=True"):
            await client.map_recovery("backup-id-123")

    async def test_map_recovery_with_confirm(self, client, mock_transport):
        await client.map_recovery("backup-id-123", confirm=True)
        mock_transport.publish.assert_called_once_with("map_recovery", {"mapId": "backup-id-123"})

    async def test_save_current_map(self, client, mock_transport):
        await client.save_current_map()
        mock_transport.publish.assert_called_once_with("save_current_map", {})

    async def test_save_map_backup_list(self, client, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value={"topic": "save_map_backup_list"})
        result = await client.save_map_backup_list()
        mock_transport.publish.assert_called_once_with(
            "save_map_backup_and_get_all_map_backup_nameandid", {}
        )
        assert isinstance(result, dict)


# ===========================================================================
# Tests: YarboClient delegation — spot-checks
# ===========================================================================


@pytest.mark.asyncio
class TestYarboClientDelegationTyped:
    async def test_shutdown_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.shutdown()
        mock_local.shutdown.assert_called_once()

    async def test_emergency_stop_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.emergency_stop()
        mock_local.emergency_stop.assert_called_once()

    async def test_set_head_light_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.set_head_light(True)
        mock_local.set_head_light.assert_called_once_with(True)

    async def test_set_sound_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.set_sound(50)
        mock_local.set_sound.assert_called_once_with(50, 0)

    async def test_start_plan_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.start_plan_direct(4, percent=80)
        mock_local.start_plan_direct.assert_called_once_with(4, 80)

    async def test_read_plan_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            result = await c.read_plan(2)
        mock_local.read_plan.assert_called_once_with(2, 5.0)
        assert result == {}

    async def test_get_speed_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            result = await c.get_speed()
        mock_local.get_speed.assert_called_once_with(5.0)
        assert result == {}

    async def test_get_wifi_list_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.get_wifi_list()
        mock_local.get_wifi_list.assert_called_once_with(5.0)

    async def test_cmd_recharge_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.cmd_recharge()
        mock_local.cmd_recharge.assert_called_once()

    async def test_in_plan_action_delegates(self, mock_local):
        async with YarboClient(broker="192.168.1.24", sn="TEST") as c:
            await c.in_plan_action("stop")
        mock_local.in_plan_action.assert_called_once_with("stop")
