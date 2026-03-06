# Telemetry, get_device_msg, and broker behaviour

## Sending get_device_msg — do we get telemetry back?

**Yes.** When the client publishes a message to the topic `snowbot/{SN}/app/get_device_msg` (empty payload `{}`), the robot responds with a **full telemetry snapshot** on the topic `snowbot/{SN}/device/data_feedback`. That payload has the same shape as a DeviceMSG (BatteryMSG, StateMSG, RTKMSG, etc.) and is parsed as `YarboTelemetry`. So one request → one telemetry snapshot. The library uses this for `get_status()` and for polling when the mobile app is disconnected.

## Is the response sent only to the client that asked, or can others see it?

**Anyone subscribed to that topic can see it.** MQTT is topic-based: the robot publishes to the **topic** `snowbot/{SN}/device/data_feedback`. The broker delivers that message to **every client that is subscribed** to that topic. It is not “addressed” to a specific IP or client. So:

- Any app or integration connected to the **same broker** and subscribed to `snowbot/{SN}/device/data_feedback` will receive the same message.
- It is not a network-level broadcast (e.g. UDP broadcast); it’s normal MQTT pub/sub on that topic.

## Two broker IPs (e.g. Rover vs DC) — which one has the data?

**Each broker is a separate MQTT server.** Typical setups have:

- **Rover** — broker on the robot (e.g. 192.168.1.55)
- **DC (Data Center)** — another broker IP (e.g. base station or another host, e.g. 192.168.1.24)

In general, traffic is **per broker**: if you send `get_device_msg` to broker A, the robot publishes the response on broker A. Broker B does not automatically get that message unless the firmware or network mirrors it.

- To see the response, connect to the broker you sent the command to; you can then rely on that connection for both publish and subscribe.
- If your integration supports “primary/fallback” (e.g. try Rover then DC), connect to one at a time and use that connection for both publish and subscribe.

### Test result (2026-03): two DC brokers

We ran `scripts/test_two_brokers_telemetry.py` with:

- **Broker1 (sender):** 192.168.1.55 — sent `get_controller` + `get_device_msg`
- **Broker2 (subscriber only):** 192.168.1.24 — only subscribed, no publish
- **SN:** 1234567890ABCD

**Result:** Both brokers received the same traffic. Broker1 received 2× `data_feedback` and 3× `heart_beat`; Broker2 received the same (2× `data_feedback`, 3× `heart_beat`). So in this setup the two broker IPs (discovered as DC and Rover) **mirror traffic** — the response to the command sent to 192.168.1.55 was visible on 192.168.1.24 as well.

**Conclusion:** With the tested firmware/network, you can use either broker IP (192.168.1.24 or 192.168.1.55) and see the same data; the system appears to keep both brokers in sync. Behaviour may differ with other setups (e.g. different firmware), so for a new deployment it is still safe to use “connect to one broker and use it for both send and subscribe.”
