"""
yarbo.cloud_mqtt — YarboCloudMqttClient: cloud MQTT control over Tencent TDMQ TLS.

Connects to the Yarbo cloud MQTT broker (Tencent TDMQ) with TLS on port 8883.
Provides the same API surface as :class:`~yarbo.local.YarboLocalClient` so that
local and cloud clients can be used interchangeably.

Broker:   ``mqtt-b8rkj5da-usw-public.mqtt.tencenttdmq.com:8883``
Auth:     Username / password (Tencent TDMQ credentials).
Transport: TLS 1.2+, server certificate validated when ``tls_ca_certs`` is supplied.

References:
    yarbo-reversing/docs/MQTT_PROTOCOL.md — protocol reference
    Tencent TDMQ MQTT documentation — broker configuration
"""

from __future__ import annotations

from .const import CLOUD_BROKER, CLOUD_PORT_TLS
from .local import YarboLocalClient
from .mqtt import MqttTransport

#: Default Tencent TDMQ username for Yarbo cloud access.
CLOUD_MQTT_DEFAULT_USERNAME = "hytech"
#: Default Tencent TDMQ password for Yarbo cloud access.
CLOUD_MQTT_DEFAULT_PASSWORD = "hy18129930886"


class YarboCloudMqttClient(YarboLocalClient):
    """
    MQTT client for the Yarbo cloud broker (Tencent TDMQ) with TLS.

    Provides the same API surface as :class:`~yarbo.local.YarboLocalClient`
    (lights, buzzer, chute, telemetry, plan management, etc.) but communicates
    over the internet via Tencent TDMQ instead of the local Wi-Fi network.

    Example (async context manager)::

        async with YarboCloudMqttClient(sn="24400102L8HO5227") as client:
            await client.lights_on()
            async for telemetry in client.watch_telemetry():
                print(f"Battery: {telemetry.battery}%")

    Args:
        sn:             Robot serial number.
        username:       Tencent TDMQ username (default: ``"hytech"``).
        password:       Tencent TDMQ password.
        broker:         MQTT broker hostname (default: Tencent TDMQ endpoint).
        port:           Broker TLS port (default: 8883).
        auto_controller: Send ``get_controller`` automatically (default True).
        tls_ca_certs:   Path to CA certificate bundle for server verification.
                        When ``None`` (default), server certificate is not
                        verified (suitable for testing; use a CA bundle in
                        production).
    """

    def __init__(
        self,
        sn: str,
        username: str = CLOUD_MQTT_DEFAULT_USERNAME,
        password: str = CLOUD_MQTT_DEFAULT_PASSWORD,
        broker: str = CLOUD_BROKER,
        port: int = CLOUD_PORT_TLS,
        auto_controller: bool = True,
        tls_ca_certs: str | None = None,
    ) -> None:
        # Initialise parent fields directly (bypass super().__init__'s
        # transport construction so we can inject our TLS-enabled transport).
        self._broker = broker
        self._sn = sn
        self._port = port
        self._auto_controller = auto_controller
        self._controller_acquired = False
        self._transport = MqttTransport(
            broker=broker,
            sn=sn,
            port=port,
            username=username,
            password=password,
            tls=True,
            tls_ca_certs=tls_ca_certs,
        )
