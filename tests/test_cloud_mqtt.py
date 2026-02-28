"""
Tests for yarbo.cloud_mqtt — YarboCloudMqttClient.

These are unit tests with a mocked MQTT broker. No real network connection
is made; TLS setup is verified by inspecting the transport configuration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo.cloud_mqtt import CLOUD_MQTT_DEFAULT_USERNAME, YarboCloudMqttClient
from yarbo.const import CLOUD_BROKER, CLOUD_PORT_TLS
from yarbo.models import TelemetryEnvelope

# Sentinel test password — never a real credential.
_TEST_PASSWORD = "test-password-sentinel"


@pytest.fixture
def mock_transport_cloud():
    """Mock MqttTransport for cloud MQTT unit tests."""
    with patch("yarbo.cloud_mqtt.MqttTransport") as MockT:  # noqa: N806
        instance = MagicMock()
        instance.connect = AsyncMock()
        instance.disconnect = AsyncMock()
        instance.publish = AsyncMock()
        instance.wait_for_message = AsyncMock(return_value=None)
        instance.create_wait_queue = MagicMock(return_value=MagicMock())
        instance.release_queue = MagicMock()
        instance.is_connected = True
        instance.add_reconnect_callback = MagicMock()

        async def fake_stream():
            yield TelemetryEnvelope(
                kind="DeviceMSG",
                payload={"BatteryMSG": {"capacity": 60}},
                topic="snowbot/TEST/device/DeviceMSG",
            )

        instance.telemetry_stream = fake_stream
        MockT.return_value = instance
        yield instance, MockT


def test_password_runtime_env_fallback(monkeypatch, mock_transport_cloud):
    """YARBO_MQTT_PASSWORD is read at construction time when password=None."""
    _, mock_t = mock_transport_cloud
    monkeypatch.setenv("YARBO_MQTT_PASSWORD", _TEST_PASSWORD)
    YarboCloudMqttClient(sn="TESTSN", password=None)
    kwargs = mock_t.call_args[1]
    assert kwargs["password"] == _TEST_PASSWORD


def test_password_runtime_env_omitted(monkeypatch, mock_transport_cloud):
    """YARBO_MQTT_PASSWORD is read at construction time when password is omitted."""
    _, mock_t = mock_transport_cloud
    monkeypatch.setenv("YARBO_MQTT_PASSWORD", _TEST_PASSWORD)
    YarboCloudMqttClient(sn="TESTSN")
    kwargs = mock_t.call_args[1]
    assert kwargs["password"] == _TEST_PASSWORD


@pytest.mark.asyncio
class TestYarboCloudMqttClientDefaults:
    async def test_default_broker_and_port(self, mock_transport_cloud):
        _, mock_t = mock_transport_cloud
        YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        kwargs = mock_t.call_args[1]
        assert kwargs["broker"] == CLOUD_BROKER
        assert kwargs["port"] == CLOUD_PORT_TLS

    async def test_default_username(self, monkeypatch, mock_transport_cloud):
        """Username should default to CLOUD_MQTT_DEFAULT_USERNAME when env is unset."""
        monkeypatch.delenv("YARBO_MQTT_USERNAME", raising=False)
        _, mock_t = mock_transport_cloud
        YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        kwargs = mock_t.call_args[1]
        assert kwargs["username"] == CLOUD_MQTT_DEFAULT_USERNAME

    async def test_username_default_from_env(self, monkeypatch, mock_transport_cloud):
        """YARBO_MQTT_USERNAME should be read at construction time when omitted."""
        monkeypatch.setenv("YARBO_MQTT_USERNAME", "env-user")
        _, mock_t = mock_transport_cloud
        YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        kwargs = mock_t.call_args[1]
        assert kwargs["username"] == "env-user"

    async def test_empty_password_raises(self, mock_transport_cloud):
        """Empty password must raise ValueError — no silent unauthenticated connections."""
        with pytest.raises(ValueError, match="password"):
            YarboCloudMqttClient(sn="TESTSN", password="")

    async def test_tls_enabled(self, mock_transport_cloud):
        _, mock_t = mock_transport_cloud
        YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        kwargs = mock_t.call_args[1]
        assert kwargs["tls"] is True

    async def test_custom_credentials(self, mock_transport_cloud):
        _, mock_t = mock_transport_cloud
        YarboCloudMqttClient(sn="TESTSN", username="myuser", password="mypass")
        kwargs = mock_t.call_args[1]
        assert kwargs["username"] == "myuser"
        assert kwargs["password"] == "mypass"

    async def test_custom_ca_certs(self, mock_transport_cloud):
        _, mock_t = mock_transport_cloud
        YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD, tls_ca_certs="/path/to/ca.pem")
        kwargs = mock_t.call_args[1]
        assert kwargs["tls_ca_certs"] == "/path/to/ca.pem"


@pytest.mark.asyncio
class TestYarboCloudMqttClientAPI:
    """Verify cloud client has the same API surface as YarboLocalClient."""

    async def test_connect_disconnect(self, mock_transport_cloud):
        transport, _ = mock_transport_cloud
        client = YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        await client.connect()
        transport.connect.assert_called_once()
        await client.disconnect()
        transport.disconnect.assert_called_once()

    async def test_context_manager(self, mock_transport_cloud):
        transport, _ = mock_transport_cloud
        async with YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD) as client:
            assert client.is_connected
        transport.connect.assert_called_once()
        transport.disconnect.assert_called_once()

    async def test_lights_on(self, mock_transport_cloud):
        transport, _ = mock_transport_cloud
        client = YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        await client.connect()
        client._controller_acquired = True
        await client.lights_on()
        cmds = [c[0][0] for c in transport.publish.call_args_list]
        assert "light_ctrl" in cmds

    async def test_serial_number(self, mock_transport_cloud):
        _, _ = mock_transport_cloud
        client = YarboCloudMqttClient(sn="24400102L8HO5227", password=_TEST_PASSWORD)
        assert client.serial_number == "24400102L8HO5227"

    async def test_watch_telemetry_yields(self, mock_transport_cloud):
        _transport, _ = mock_transport_cloud
        client = YarboCloudMqttClient(sn="TESTSN", password=_TEST_PASSWORD)
        await client.connect()
        items = []
        async for t in client.watch_telemetry():
            items.append(t)
            break
        assert len(items) == 1
        assert items[0].battery == 60
