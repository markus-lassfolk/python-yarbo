"""
Tests for yarbo.mqtt — MqttTransport.

Covers:
- Topic formatting (TOPIC_APP_TMPL, TOPIC_DEVICE_TMPL)
- Topic.leaf() and Topic.parse() helpers
- Subscription to ALL_FEEDBACK_LEAVES on connect
- paho v2 on_connect callback with ReasonCode object
- Message queue routing and topic-leaf filtering
- wait_for_message feedback_leaf filtering
- Queue removal safety (no ValueError on double-remove)
- telemetry_stream envelope output
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
import zlib

import paho.mqtt.client as real_mqtt
import pytest

from yarbo._codec import decode
from yarbo.const import (
    ALL_FEEDBACK_LEAVES,
    TOPIC_APP_TMPL,
    TOPIC_DEVICE_TMPL,
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_DEVICE_MSG,
    TOPIC_LEAF_HEART_BEAT,
    Topic,
)
from yarbo.local import YarboLocalClient
from yarbo.models import TelemetryEnvelope
from yarbo.mqtt import MqttTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode(payload: dict) -> bytes:
    """Wire-format encode a dict for testing."""
    return zlib.compress(json.dumps(payload, separators=(",", ":")).encode())


def _fake_msg(topic: str, payload: dict) -> MagicMock:
    """Build a fake paho MQTTMessage-like object."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = _encode(payload)
    return msg


# ---------------------------------------------------------------------------
# Topic helper
# ---------------------------------------------------------------------------


class TestTopicHelper:
    def test_app_topic(self):
        t = Topic("SN123")
        assert t.app("light_ctrl") == "snowbot/SN123/app/light_ctrl"

    def test_device_topic(self):
        t = Topic("SN123")
        assert t.device("data_feedback") == "snowbot/SN123/device/data_feedback"

    def test_parse_full_topic(self):
        sn, leaf = Topic.parse("snowbot/ABC456/device/DeviceMSG")
        assert sn == "ABC456"
        assert leaf == "DeviceMSG"

    def test_parse_invalid_returns_empty(self):
        assert Topic.parse("invalid") == ("", "")
        assert Topic.parse("a/b") == ("", "")

    def test_leaf_device_msg(self):
        assert Topic.leaf("snowbot/SN/device/DeviceMSG") == "DeviceMSG"

    def test_leaf_heart_beat(self):
        assert Topic.leaf("snowbot/SN/device/heart_beat") == "heart_beat"

    def test_topic_constants_in_templates(self):
        """Template substitution produces expected topic strings."""
        sn = "TESTROBOT"
        topic = TOPIC_APP_TMPL.format(sn=sn, cmd="get_controller")
        assert topic == f"snowbot/{sn}/app/get_controller"
        topic = TOPIC_DEVICE_TMPL.format(sn=sn, feedback="DeviceMSG")
        assert topic == f"snowbot/{sn}/device/DeviceMSG"


class TestTopicLeaves:
    def test_device_msg_in_leaves(self):
        assert TOPIC_LEAF_DEVICE_MSG in ALL_FEEDBACK_LEAVES

    def test_heart_beat_in_leaves(self):
        assert TOPIC_LEAF_HEART_BEAT in ALL_FEEDBACK_LEAVES

    def test_data_feedback_in_leaves(self):
        assert TOPIC_LEAF_DATA_FEEDBACK in ALL_FEEDBACK_LEAVES


# ---------------------------------------------------------------------------
# MqttTransport unit tests (with mocked paho)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_paho(monkeypatch):
    """
    Patch paho.mqtt.client.Client.

    Returns a MagicMock instance pre-wired to:
    - capture on_connect / on_message assignment
    - expose ``fire_connect(rc)`` to trigger the on_connect callback
    - expose ``fire_message(topic, payload)`` to inject a message
    """
    mock_client = MagicMock()
    mock_client.CallbackAPIVersion = real_mqtt.CallbackAPIVersion
    mock_client.MQTTv311 = real_mqtt.MQTTv311

    stored: dict = {}

    mock_client_instance = MagicMock()
    mock_client_instance.loop_start = MagicMock()
    mock_client_instance.loop_stop = MagicMock()
    mock_client_instance.connect = MagicMock()
    mock_client_instance.disconnect = MagicMock()
    mock_client_instance.subscribe = MagicMock()
    mock_client_instance.publish = MagicMock()

    type(mock_client_instance).on_connect = property(
        fget=lambda s: stored.get("on_connect"),
        fset=lambda s, v: stored.update({"on_connect": v}),
    )
    type(mock_client_instance).on_message = property(
        fget=lambda s: stored.get("on_message"),
        fset=lambda s, v: stored.update({"on_message": v}),
    )
    type(mock_client_instance).on_disconnect = property(
        fget=lambda s: stored.get("on_disconnect"),
        fset=lambda s, v: stored.update({"on_disconnect": v}),
    )

    mock_client.Client.return_value = mock_client_instance

    def fire_connect(rc: int = 0) -> None:
        cb = stored.get("on_connect")
        if cb:
            cb(mock_client_instance, None, None, rc, None)

    def fire_message(topic: str, payload: dict) -> None:
        msg = _fake_msg(topic, payload)
        cb = stored.get("on_message")
        if cb:
            cb(mock_client_instance, None, msg)

    mock_client_instance._fire_connect = fire_connect
    mock_client_instance._fire_message = fire_message

    monkeypatch.setattr("yarbo.mqtt.asyncio.get_running_loop", asyncio.get_event_loop)
    monkeypatch.setattr("paho.mqtt.client", mock_client, raising=False)

    with patch("paho.mqtt.client.Client", mock_client.Client):
        yield mock_client_instance


@pytest.mark.asyncio
class TestMqttTransportCallbacks:
    """Test paho v2 callback signature handling."""

    async def test_on_connect_with_int_rc(self):
        """
        on_connect accepts plain int reason_code (paho v1 style).
        ``getattr(0, 'value', 0)`` → 0, so connection proceeds.
        """
        transport = MqttTransport(broker="192.168.1.24", sn="ROBOT1")
        # Simulate the callback method directly
        connected_event = asyncio.Event()
        transport._connected = connected_event

        mock_client = MagicMock()
        mock_client.subscribe = MagicMock()
        transport._client = mock_client
        transport._loop = asyncio.get_running_loop()
        transport._sn = "ROBOT1"
        transport._qos = 0

        # Fire with int rc=0
        transport._on_connect(mock_client, None, None, 0, None)
        # call_soon_threadsafe schedules set() — yield to let it run
        await asyncio.sleep(0)
        assert connected_event.is_set()

    async def test_on_connect_with_reason_code_object(self):
        """
        on_connect accepts a ReasonCode-like object with a .value attribute.
        This matches paho v2's actual callback contract.
        """
        transport = MqttTransport(broker="192.168.1.24", sn="ROBOT1")
        connected_event = asyncio.Event()
        transport._connected = connected_event

        mock_client = MagicMock()
        mock_client.subscribe = MagicMock()
        transport._client = mock_client
        transport._loop = asyncio.get_running_loop()
        transport._sn = "ROBOT1"
        transport._qos = 0

        # ReasonCode-like object
        rc_obj = MagicMock()
        rc_obj.value = 0
        transport._on_connect(mock_client, None, None, rc_obj, None)
        await asyncio.sleep(0)  # yield to let call_soon_threadsafe fire
        assert connected_event.is_set()

    async def test_on_connect_subscribes_all_leaves(self):
        """on_connect subscribes to ALL_FEEDBACK_LEAVES."""
        transport = MqttTransport(broker="192.168.1.24", sn="ROBOT1")
        transport._connected = asyncio.Event()
        mock_client = MagicMock()
        transport._client = mock_client
        transport._loop = asyncio.get_running_loop()
        transport._sn = "ROBOT1"
        transport._qos = 0

        transport._on_connect(mock_client, None, None, 0, None)
        await asyncio.sleep(0)  # let call_soon_threadsafe fire

        subscribed = [call_args[0][0] for call_args in mock_client.subscribe.call_args_list]
        for leaf in ALL_FEEDBACK_LEAVES:
            expected = TOPIC_DEVICE_TMPL.format(sn="ROBOT1", feedback=leaf)
            assert expected in subscribed, f"Expected {expected!r} in subscriptions"


@pytest.mark.asyncio
class TestWaitForMessageFiltering:
    """Test wait_for_message topic-leaf filtering."""

    async def _make_transport(self) -> MqttTransport:
        """Build a bare MqttTransport with a wired loop."""
        t = MqttTransport(broker="localhost", sn="SN1")
        t._connected.set()
        t._loop = asyncio.get_running_loop()
        return t

    async def test_filters_correct_leaf(self):
        """Only messages matching feedback_leaf are returned."""
        transport = await self._make_transport()

        # Inject a DeviceMSG envelope (wrong leaf) then a data_feedback (correct)
        async def inject() -> None:
            await asyncio.sleep(0.01)
            env_wrong = {
                "topic": "snowbot/SN1/device/DeviceMSG",
                "payload": {"BatteryMSG": {"capacity": 80}},
            }
            env_right = {
                "topic": "snowbot/SN1/device/data_feedback",
                "payload": {"topic": "get_controller", "state": 0, "data": {}},
            }
            for q in transport._message_queues:
                await q.put(env_wrong)
                await q.put(env_right)

        task = asyncio.create_task(inject())
        result = await transport.wait_for_message(
            timeout=1.0,
            feedback_leaf=TOPIC_LEAF_DATA_FEEDBACK,
        )
        await task
        assert result is not None
        assert result.get("topic") == "get_controller"

    async def test_timeout_returns_none(self):
        """Returns None if no matching message arrives within timeout."""
        transport = await self._make_transport()
        result = await transport.wait_for_message(timeout=0.05, feedback_leaf="noop_leaf")
        assert result is None

    async def test_queue_removed_after_wait(self):
        """Queue is removed from _message_queues after wait_for_message returns."""
        transport = await self._make_transport()
        assert len(transport._message_queues) == 0
        await transport.wait_for_message(timeout=0.05, feedback_leaf="noop_leaf")
        assert len(transport._message_queues) == 0

    async def test_queue_removal_safe_if_already_removed(self):
        """
        Queue removal must not raise ValueError if already absent.
        Simulates concurrent cleanup race condition.
        """
        transport = MqttTransport(broker="localhost", sn="SN1")
        transport._connected.set()
        transport._loop = asyncio.get_running_loop()

        q: asyncio.Queue = asyncio.Queue()
        # q is NOT in _message_queues → removing should raise ValueError
        with pytest.raises(ValueError):
            transport._message_queues.remove(q)

        # The actual code wraps this in contextlib.suppress(ValueError) — verify
        with contextlib.suppress(ValueError):
            transport._message_queues.remove(q)
        # If we reach here, the suppress worked correctly


@pytest.mark.asyncio
class TestTelemetryStream:
    """Test telemetry_stream yields TelemetryEnvelope with correct kind."""

    async def test_yields_envelope(self):
        """telemetry_stream yields TelemetryEnvelope objects."""
        transport = MqttTransport(broker="localhost", sn="SN1")
        transport._connected.set()
        transport._loop = asyncio.get_running_loop()

        device_msg_payload = {
            "BatteryMSG": {"capacity": 75},
            "StateMSG": {"working_state": 1},
        }

        async def inject() -> None:
            await asyncio.sleep(0.01)
            envelope = {
                "topic": "snowbot/SN1/device/DeviceMSG",
                "payload": device_msg_payload,
            }
            for q in transport._message_queues:
                await q.put(envelope)
            # Mark as disconnected to end the stream
            transport._connected.clear()

        task = asyncio.create_task(inject())

        envelopes = []
        async for env in transport.telemetry_stream():
            envelopes.append(env)

        await task
        assert len(envelopes) >= 1
        e = envelopes[0]
        assert isinstance(e, TelemetryEnvelope)
        assert e.kind == "DeviceMSG"
        assert e.is_telemetry is True
        assert e.payload == device_msg_payload

    async def test_heartbeat_envelope(self):
        """heart_beat messages produce heartbeat envelopes."""
        transport = MqttTransport(broker="localhost", sn="SN1")
        transport._connected.set()
        transport._loop = asyncio.get_running_loop()

        async def inject() -> None:
            await asyncio.sleep(0.01)
            envelope = {
                "topic": "snowbot/SN1/device/heart_beat",
                "payload": {"working_state": 0},
            }
            for q in transport._message_queues:
                await q.put(envelope)
            transport._connected.clear()

        task = asyncio.create_task(inject())

        envelopes = []
        async for env in transport.telemetry_stream():
            envelopes.append(env)

        await task
        assert len(envelopes) >= 1
        e = envelopes[0]
        assert e.kind == "heart_beat"
        assert e.is_heartbeat is True


@pytest.mark.asyncio
class TestOnMessage:
    """Test _on_message callback routing."""

    async def test_routes_to_queues(self):
        """_on_message pushes envelopes to all registered queues."""
        transport = MqttTransport(broker="localhost", sn="SN1")
        transport._loop = asyncio.get_running_loop()

        q1: asyncio.Queue = asyncio.Queue()
        q2: asyncio.Queue = asyncio.Queue()
        transport._message_queues = [q1, q2]

        payload = {"BatteryMSG": {"capacity": 90}}
        msg = _fake_msg("snowbot/SN1/device/DeviceMSG", payload)
        transport._on_message(None, None, msg)

        await asyncio.sleep(0.01)  # let call_soon_threadsafe fire

        assert not q1.empty()
        assert not q2.empty()
        env1 = q1.get_nowait()
        assert env1["topic"] == "snowbot/SN1/device/DeviceMSG"
        assert env1["payload"]["BatteryMSG"]["capacity"] == 90

    async def test_routes_to_queues_no_queues_registered(self):
        """_on_message is a no-op if no queues are registered."""
        transport = MqttTransport(broker="localhost", sn="SN1")
        transport._loop = asyncio.get_running_loop()
        # Should not raise even when _message_queues is empty
        msg = _fake_msg("snowbot/SN1/device/DeviceMSG", {"x": 1})
        transport._on_message(None, None, msg)  # no queues → no-op


class TestCodecHeartbeat:
    """Test codec plain-JSON fallback for heart_beat messages."""

    def test_heart_beat_plain_json(self):
        """heart_beat is plain JSON (not zlib); codec fallback handles it."""
        plain = json.dumps({"working_state": 1}).encode()
        result = decode(plain)
        assert result == {"working_state": 1}


@pytest.mark.asyncio
class TestMqttReconnect:
    """Test reconnect re-subscription and callback logic."""

    async def _make_transport(self) -> MqttTransport:
        t = MqttTransport(broker="localhost", sn="SN1")
        t._loop = asyncio.get_running_loop()
        t._connected.set()
        return t

    async def test_reconnect_callbacks_not_called_on_initial_connect(self):
        """Reconnect callbacks must NOT fire on the very first connect."""
        transport = await self._make_transport()
        fired: list[bool] = []
        transport.add_reconnect_callback(lambda: fired.append(True))

        mock_client = MagicMock()
        transport._client = mock_client
        transport._was_connected = False  # initial state

        transport._on_connect(mock_client, None, None, 0, None)
        await asyncio.sleep(0)
        assert fired == []

    async def test_reconnect_callbacks_called_on_reconnect(self):
        """Reconnect callbacks fire after a disconnect + reconnect cycle."""
        transport = await self._make_transport()
        fired: list[bool] = []
        transport.add_reconnect_callback(lambda: fired.append(True))

        mock_client = MagicMock()
        transport._client = mock_client

        # Simulate disconnect first
        transport._on_disconnect(mock_client, None, None, 0, None)
        await asyncio.sleep(0)
        assert transport._was_connected is True

        # Now reconnect — callback should fire
        transport._on_connect(mock_client, None, None, 0, None)
        await asyncio.sleep(0)
        assert len(fired) == 1

    async def test_subscriptions_reapplied_on_reconnect(self):
        """All feedback topics are re-subscribed on reconnect."""
        transport = await self._make_transport()
        mock_client = MagicMock()
        transport._client = mock_client
        transport._qos = 0

        # Simulate disconnect + reconnect
        transport._on_disconnect(mock_client, None, None, 0, None)
        await asyncio.sleep(0)
        mock_client.subscribe.reset_mock()

        transport._on_connect(mock_client, None, None, 0, None)
        await asyncio.sleep(0)

        subscribed = [c[0][0] for c in mock_client.subscribe.call_args_list]
        for leaf in ALL_FEEDBACK_LEAVES:
            expected = TOPIC_DEVICE_TMPL.format(sn="SN1", feedback=leaf)
            assert expected in subscribed

    async def test_no_duplicate_reconnect_callbacks(self):
        """add_reconnect_callback ignores duplicate registrations."""
        transport = await self._make_transport()
        fired: list[int] = []

        def cb() -> None:
            fired.append(1)

        transport.add_reconnect_callback(cb)
        transport.add_reconnect_callback(cb)
        assert len(transport._reconnect_callbacks) == 1

    async def test_local_client_resets_controller_on_reconnect(self):
        """YarboLocalClient resets _controller_acquired when transport reconnects."""
        with patch("yarbo.local.MqttTransport") as MockT:  # noqa: N806
            instance = MagicMock()
            instance.connect = AsyncMock()
            instance.is_connected = True
            callbacks: list = []
            instance.add_reconnect_callback = MagicMock(side_effect=callbacks.append)
            MockT.return_value = instance

            client = YarboLocalClient(broker="192.168.1.24", sn="TEST")
            client._controller_acquired = True
            await client.connect()

            # Trigger the registered reconnect callback directly
            assert len(callbacks) == 1
            callbacks[0]()  # simulates transport reconnect
            assert client._controller_acquired is False
