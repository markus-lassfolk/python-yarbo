"""Tests for yarbo._cli — CLI entry point."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo._cli import (
    _add_connection_args,
    _apply_debug_env,
    _maybe_report_mqtt,
    _mqtt_capture_max,
    _run_discover,
    _run_status,
    _with_client,
)
from yarbo.discovery import YarboEndpoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    """Build a Namespace with sensible CLI defaults."""
    defaults = {
        "broker": None,
        "serial": None,
        "port": 1883,
        "subnet": None,
        "timeout": 5.0,
        "max_hosts": 512,
        "log_mqtt": None,
        "debug": False,
        "debug_raw": False,
        "report_mqtt": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_endpoint(ip: str = "192.0.2.1", recommended: bool = True) -> YarboEndpoint:
    return YarboEndpoint(
        ip=ip,
        port=1883,
        path="rover",
        mac="c8:fe:0f:11:22:33",
        recommended=recommended,
        hostname=None,
        sn="TESTSN001",
    )


# ---------------------------------------------------------------------------
# _add_connection_args
# ---------------------------------------------------------------------------


class TestAddConnectionArgs:
    def test_has_max_hosts(self):
        """`--max-hosts` is present so all subcommands honour it."""
        parser = argparse.ArgumentParser()
        _add_connection_args(parser)
        args = parser.parse_args(["--max-hosts", "64"])
        assert args.max_hosts == 64

    def test_max_hosts_default(self):
        """Default value for --max-hosts is 512."""
        parser = argparse.ArgumentParser()
        _add_connection_args(parser)
        args = parser.parse_args([])
        assert args.max_hosts == 512

    def test_has_all_core_flags(self):
        """All expected connection flags are registered."""
        parser = argparse.ArgumentParser()
        _add_connection_args(parser)
        args = parser.parse_args(["--broker", "10.0.0.1", "--sn", "SN1", "--port", "1883"])
        assert args.broker == "10.0.0.1"
        assert args.serial == "SN1"
        assert args.port == 1883

    def test_has_debug_raw_report_mqtt_flags(self):
        """--debug, --raw, and --report-mqtt are registered for troubleshooting."""
        parser = argparse.ArgumentParser()
        _add_connection_args(parser)
        args = parser.parse_args(["--debug", "--raw", "--report-mqtt"])
        assert args.debug is True
        assert args.debug_raw is True
        assert args.report_mqtt is True


# ---------------------------------------------------------------------------
# _with_client — explicit broker/sn path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWithClientExplicit:
    async def test_explicit_broker_connects_directly(self):
        """When --broker and --sn given, connect without calling discover."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        args = _make_args(broker="192.0.2.1", serial="SN1")
        yielded = []

        with patch("yarbo._cli.YarboLocalClient", return_value=mock_client):
            async for client, ip in _with_client(args):
                yielded.append((client, ip))

        assert len(yielded) == 1
        client, ip = yielded[0]
        assert ip == "192.0.2.1"
        mock_client.connect.assert_awaited_once()
        mock_client.disconnect.assert_awaited_once()

    async def test_passes_debug_and_capture_to_client(self):
        """When --debug, --raw, or --report-mqtt are set, YarboLocalClient receives them."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        args = _make_args(
            broker="192.0.2.1",
            serial="SN1",
            debug=True,
            debug_raw=True,
            report_mqtt=True,
        )

        with patch("yarbo._cli.YarboLocalClient", return_value=mock_client) as mock_cls:
            async for _ in _with_client(args):
                pass

        mock_cls.assert_called_once()
        call_kw = mock_cls.call_args.kwargs
        assert call_kw["debug"] is True
        assert call_kw["debug_raw"] is True
        assert call_kw["mqtt_capture_max"] == 1000

    async def test_explicit_does_not_call_discover(self):
        """Explicit broker/sn path must not call discover()."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        args = _make_args(broker="192.0.2.1", serial="SN1")

        with (
            patch("yarbo._cli.YarboLocalClient", return_value=mock_client),
            patch("yarbo._cli.discover") as mock_discover,
        ):
            async for _ in _with_client(args):
                pass

        mock_discover.assert_not_called()


# ---------------------------------------------------------------------------
# _with_client — auto-discover path with failover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWithClientDiscover:
    async def test_uses_recommended_endpoint_first(self):
        """connection_order puts recommended endpoint first; _with_client tries it."""
        ep_a = _make_endpoint(ip="192.0.2.1", recommended=False)
        ep_b = _make_endpoint(ip="192.0.2.2", recommended=True)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        yielded_ips = []

        with (
            patch("yarbo._cli.discover", AsyncMock(return_value=[ep_a, ep_b])),
            patch("yarbo._cli.YarboLocalClient", return_value=mock_client),
        ):
            async for _, ip in _with_client(_make_args()):
                yielded_ips.append(ip)

        assert yielded_ips == ["192.0.2.2"]

    async def test_falls_back_to_next_endpoint_on_connect_error(self):
        """When the first endpoint fails to connect, the next is tried."""
        ep_a = _make_endpoint(ip="192.0.2.1", recommended=True)
        ep_b = _make_endpoint(ip="192.0.2.2", recommended=False)

        good_client = MagicMock()
        good_client.connect = AsyncMock()
        good_client.disconnect = AsyncMock()

        bad_client = MagicMock()
        bad_client.connect = AsyncMock(side_effect=OSError("refused"))
        bad_client.disconnect = AsyncMock()

        clients = [bad_client, good_client]
        yielded_ips = []

        with (
            patch("yarbo._cli.discover", AsyncMock(return_value=[ep_a, ep_b])),
            patch("yarbo._cli.YarboLocalClient", side_effect=clients),
        ):
            async for _, ip in _with_client(_make_args()):
                yielded_ips.append(ip)

        assert yielded_ips == ["192.0.2.2"]

    async def test_raises_system_exit_when_no_endpoints(self):
        """SystemExit raised with helpful message when no endpoints found."""
        with (
            patch("yarbo._cli.discover", AsyncMock(return_value=[])),
            pytest.raises(SystemExit),
        ):
            async for _ in _with_client(_make_args()):
                pass

    async def test_raises_system_exit_when_all_fail(self):
        """SystemExit raised when all discovered endpoints fail."""
        ep = _make_endpoint()
        bad_client = MagicMock()
        bad_client.connect = AsyncMock(side_effect=OSError("refused"))
        bad_client.disconnect = AsyncMock()

        with (
            patch("yarbo._cli.discover", AsyncMock(return_value=[ep])),
            patch("yarbo._cli.YarboLocalClient", return_value=bad_client),
            pytest.raises(SystemExit),
        ):
            async for _ in _with_client(_make_args()):
                pass

    async def test_passes_max_hosts_to_discover(self):
        """_with_client forwards --max-hosts to discover()."""
        mock_discover = AsyncMock(return_value=[])
        args = _make_args(max_hosts=64)

        with (
            patch("yarbo._cli.discover", mock_discover),
            pytest.raises(SystemExit),
        ):
            async for _ in _with_client(args):
                pass

        mock_discover.assert_awaited_once()
        _, kwargs = mock_discover.call_args
        assert kwargs.get("max_hosts") == 64


# ---------------------------------------------------------------------------
# _run_discover command path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunDiscover:
    async def test_prints_no_endpoints_when_empty(self, capsys):
        """'No Yarbo endpoints found.' printed and exits 1 when discover returns []."""
        args = argparse.Namespace(subnet=None, timeout=5.0, port=1883, max_hosts=512)
        with patch("yarbo._cli.discover", AsyncMock(return_value=[])):
            with pytest.raises(SystemExit) as exc_info:
                await _run_discover(args)
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "No Yarbo endpoints found" in out

    async def test_prints_endpoints_table(self, capsys):
        """Discovered endpoints are printed in a table."""
        ep = _make_endpoint()
        args = argparse.Namespace(subnet=None, timeout=5.0, port=1883, max_hosts=512)
        with patch("yarbo._cli.discover", AsyncMock(return_value=[ep])):
            await _run_discover(args)
        out = capsys.readouterr().out
        assert "192.0.2.1" in out
        assert "TESTSN001" in out


# ---------------------------------------------------------------------------
# _run_status command path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunStatus:
    async def test_explicit_broker_prints_status(self, capsys):
        """With --broker and --sn, status is fetched and printed."""
        mock_status = MagicMock()
        mock_status.sn = "SN1"
        mock_status.name = None
        mock_status.head_type = None
        mock_status.battery = 80
        mock_status.working_state = 0
        mock_status.charging_status = None
        mock_status.error_code = None
        mock_status.rtk_status = None
        mock_status.heading = None
        mock_status.last_updated = None
        mock_status.head_serial_number = None
        mock_status.battery_status = None
        mock_status.on_going_planning = None
        mock_status.planning_paused = None
        mock_status.on_going_recharging = None
        mock_status.car_controller = None
        mock_status.machine_controller = None
        mock_status.position_x = None
        mock_status.position_y = None
        mock_status.phi = None
        mock_status.odom_confidence = None
        mock_status.chute_angle = None
        mock_status.led = None
        mock_status.wireless_charge_voltage = None
        mock_status.wireless_charge_current = None
        mock_status.route_priority = None
        mock_status.state = "idle"
        mock_status.speed = None
        mock_status.latitude = None
        mock_status.longitude = None
        mock_status.altitude = None
        mock_status.fix_quality = 0
        mock_status.all_mqtt_values = MagicMock(return_value={})

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.get_status = AsyncMock(return_value=mock_status)

        args = _make_args(broker="192.0.2.1", serial="SN1")

        with patch("yarbo._cli.YarboLocalClient", return_value=mock_client):
            await _run_status(args)

        out = capsys.readouterr().out
        assert "SN1" in out or "80" in out

    async def test_explicit_broker_exits_nonzero_when_no_status(self):
        """Non-zero exit when broker/sn given but no telemetry received."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.get_status = AsyncMock(return_value=None)

        args = _make_args(broker="192.0.2.1", serial="SN1")

        with (
            patch("yarbo._cli.YarboLocalClient", return_value=mock_client),
            pytest.raises(SystemExit) as exc_info,
        ):
            await _run_status(args)

        assert exc_info.value.code != 0

    async def test_discover_path_exits_nonzero_when_no_endpoints(self):
        """SystemExit raised when discover returns [] (consistent with other failures)."""
        args = _make_args()
        with (
            patch("yarbo._cli.discover", AsyncMock(return_value=[])),
            pytest.raises(SystemExit),
        ):
            await _run_status(args)

    async def test_report_mqtt_called_when_flag_set(self):
        """When --report-mqtt is set, report_mqtt_dump_to_glitchtip is called after command."""
        mock_status = MagicMock()
        mock_status.sn = "SN1"
        mock_status.name = None
        mock_status.head_type = None
        mock_status.battery = 80
        mock_status.working_state = 0
        mock_status.charging_status = None
        mock_status.error_code = None
        mock_status.rtk_status = None
        mock_status.heading = None
        mock_status.last_updated = None
        mock_status.head_serial_number = None
        mock_status.battery_status = None
        mock_status.on_going_planning = None
        mock_status.planning_paused = None
        mock_status.on_going_recharging = None
        mock_status.car_controller = None
        mock_status.machine_controller = None
        mock_status.position_x = None
        mock_status.position_y = None
        mock_status.phi = None
        mock_status.odom_confidence = None
        mock_status.chute_angle = None
        mock_status.led = None
        mock_status.wireless_charge_voltage = None
        mock_status.wireless_charge_current = None
        mock_status.route_priority = None
        mock_status.state = "idle"
        mock_status.speed = None
        mock_status.latitude = None
        mock_status.longitude = None
        mock_status.altitude = None
        mock_status.fix_quality = 0
        mock_status.all_mqtt_values = MagicMock(return_value={})

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.get_status = AsyncMock(return_value=mock_status)
        mock_client.get_captured_mqtt = MagicMock(return_value=[{"topic": "t", "payload": {}}])

        args = _make_args(broker="192.0.2.1", serial="SN1", report_mqtt=True)

        with (
            patch("yarbo._cli.YarboLocalClient", return_value=mock_client),
            patch("yarbo._cli.report_mqtt_dump_to_glitchtip") as mock_report,
        ):
            await _run_status(args)

        mock_report.assert_called_once()
        assert mock_report.call_args[0][0] == [{"topic": "t", "payload": {}}]
        mock_client.get_captured_mqtt.assert_called_once()


# ---------------------------------------------------------------------------
# _mqtt_capture_max, _maybe_report_mqtt
# ---------------------------------------------------------------------------


class TestMqttCaptureMax:
    def test_zero_when_report_mqtt_false(self):
        args = _make_args(report_mqtt=False)
        assert _mqtt_capture_max(args) == 0

    def test_1000_when_report_mqtt_true(self):
        args = _make_args(report_mqtt=True)
        assert _mqtt_capture_max(args) == 1000


class TestMaybeReportMqtt:
    def test_calls_report_when_report_mqtt_set(self):
        args = _make_args(report_mqtt=True)
        mock_client = MagicMock()
        mock_client.get_captured_mqtt = MagicMock(return_value=[{"topic": "x", "payload": {}}])
        with patch("yarbo._cli.report_mqtt_dump_to_glitchtip") as mock_report:
            _maybe_report_mqtt(args, mock_client)
        mock_report.assert_called_once_with([{"topic": "x", "payload": {}}])
        mock_client.get_captured_mqtt.assert_called_once()

    def test_no_op_when_report_mqtt_false(self):
        args = _make_args(report_mqtt=False)
        mock_client = MagicMock()
        with patch("yarbo._cli.report_mqtt_dump_to_glitchtip") as mock_report:
            _maybe_report_mqtt(args, mock_client)
        mock_report.assert_not_called()


# ---------------------------------------------------------------------------
# _apply_debug_env — YARBO_DEBUG / YARBO_DEBUG_RAW
# ---------------------------------------------------------------------------


class TestApplyDebugEnv:
    def test_sets_debug_from_yarbo_debug_env(self, monkeypatch):
        """YARBO_DEBUG=1 (or true/yes) enables debug when --debug was not passed."""
        args = _make_args(debug=False)
        monkeypatch.setenv("YARBO_DEBUG", "1")
        _apply_debug_env(args)
        assert args.debug is True

    def test_sets_debug_raw_from_yarbo_debug_raw_env(self, monkeypatch):
        """YARBO_DEBUG_RAW=true enables raw debug when --raw was not passed."""
        args = _make_args(debug_raw=False)
        monkeypatch.setenv("YARBO_DEBUG_RAW", "true")
        _apply_debug_env(args)
        assert args.debug_raw is True

    def test_cli_debug_overrides_env(self, monkeypatch):
        """Explicit --debug=True leaves debug True even if env is unset."""
        args = _make_args(debug=True)
        monkeypatch.delenv("YARBO_DEBUG", raising=False)
        _apply_debug_env(args)
        assert args.debug is True

    def test_debug_stays_false_when_env_empty(self, monkeypatch):
        """When YARBO_DEBUG is empty or unset, debug remains False."""
        args = _make_args(debug=False)
        monkeypatch.delenv("YARBO_DEBUG", raising=False)
        _apply_debug_env(args)
        assert args.debug is False
        monkeypatch.setenv("YARBO_DEBUG", "")
        _apply_debug_env(args)
        assert args.debug is False
