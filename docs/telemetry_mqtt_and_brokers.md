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

---

## Coexistence with the mobile app (optional get_controller)

**Finding:** The robot responds to `get_device_msg` **without** requiring `get_controller`. We tested with the mobile app in control:

- **With get_controller:** Sent get_controller then get_device_msg every 10s → received status response for every request (e.g. 3/3).
- **Without get_controller:** Sent only get_device_msg every 10s → received status response for every request (e.g. 4/4).

So telemetry polling does **not** need to take controller. That lets the app stay in control while your integration (e.g. Home Assistant) receives telemetry.

**API:** Controller is optional for telemetry-only use:

- **`get_status(acquire_controller=False)`** (default) — Sends only `get_device_msg`; does not call `get_controller`. Use this when you only need a snapshot and want the app to keep control.
- **`get_status(acquire_controller=True)`** — Calls `get_controller` then `get_device_msg` (previous behaviour). Use only if your setup requires it.
- **`start_polling(..., acquire_controller=False)`** (default) — Poll loop sends only `get_device_msg`; does not call `get_controller`. Safe for coexistence with the app.
- **`start_polling(..., acquire_controller=True)`** — Calls `get_controller` before starting the poll loop (may take control from the app).

**When to call get_controller:** Call `get_controller()` (or use `acquire_controller=True`) only when you are about to send **action commands** (lights, buzzer, chute, plans, manual drive, etc.). For telemetry only (get_status, watch_telemetry, polling), use the defaults so the app can remain in control.

---

## Why didn’t Home Assistant get updates when I ran the script?

If you run `test_polling_with_app_in_control.py` (or any client that sends `get_device_msg`) against 192.168.1.55 and your HASS sensor (e.g. “last seen”) still doesn’t update, the cause is usually one of these:

### 1. **Different topic for “last seen”**

The robot has two ways it sends telemetry:

- **`DeviceMSG`** — Streamed at ~1 Hz **only while the mobile app is connected**. Topic: `snowbot/{SN}/device/DeviceMSG`.
- **`data_feedback`** — Response to a **request** (e.g. `get_device_msg`). Topic: `snowbot/{SN}/device/data_feedback`. Same payload shape as DeviceMSG.

When our script runs, it triggers a response on **`data_feedback`**, not on `DeviceMSG`. The robot does not start streaming `DeviceMSG` again just because we sent `get_device_msg`.

So if the HASS integration updates “last seen” **only** when it receives **`DeviceMSG`**, it will never see the traffic from the script. It will only see updates when the app is connected and the robot is streaming `DeviceMSG`.

**What the integration should do:** Treat **both** as “activity” for last seen:

- Subscribe to `snowbot/{SN}/device/DeviceMSG` **and** `snowbot/{SN}/device/data_feedback`.
- For `data_feedback`, consider messages that look like telemetry (e.g. contain `BatteryMSG` or `StateMSG`) as activity and update “last seen” (and state) from them.

Then any client that sends `get_device_msg` (including the integration itself when polling) will cause traffic that updates last seen.

### 2. **Integration doesn’t poll**

If the integration only **subscribes** and never sends `get_device_msg`, then when the app is disconnected there is no one asking for telemetry. The robot then only publishes `heart_beat` (no full telemetry). So “last seen” might be driven only by `DeviceMSG`, which has stopped.

**What the integration should do:** When it needs telemetry (e.g. for sensors / last seen), it should **run polling**: call `start_polling()` (or periodically `get_status()`). That sends `get_device_msg` and receives telemetry on `data_feedback`. If the integration also updates last seen from `data_feedback` (see above), its own polling will keep “last seen” up to date.

### 3. **Different broker or not subscribed**

- **Same broker:** HASS must be connected to the **same** MQTT broker as the script (e.g. 192.168.1.55). If the integration is configured for 192.168.1.55 but actually uses another broker (e.g. a central Mosquitto that doesn’t receive Yarbo traffic), it won’t see the messages.
- **Subscribed:** The integration must subscribe to the topic that carries the response — i.e. `data_feedback` (and ideally `DeviceMSG` too). If it only subscribes to `DeviceMSG`, it will never see the replies to `get_device_msg`.

**Summary for hass-yarbo:** Use the same broker as the script (e.g. 192.168.1.55). Subscribe to both `DeviceMSG` and `data_feedback`. Update “last seen” (and sensor state) from **both** DeviceMSG and from data_feedback messages that contain telemetry. Run polling (e.g. `start_polling()`) so the robot is asked for telemetry periodically; then the integration will receive that telemetry on `data_feedback` and can update “last seen” even when the app is disconnected.
