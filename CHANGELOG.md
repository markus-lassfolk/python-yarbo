# Changelog

All notable changes to python-yarbo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **CLI (`yarbo`)** — full-featured command-line interface
  - `yarbo discover` — find Rover/DC brokers (optional `--subnet`, `--max-hosts`)
  - `yarbo status` — connect with primary/fallback, print full telemetry (parsed + all MQTT keys)
  - `yarbo battery`, `yarbo telemetry` — status and live stream
  - `yarbo lights-on`, `yarbo lights-off`, `yarbo buzzer`, `yarbo chute`, `yarbo return-to-dock`
  - `yarbo plans`, `yarbo plan-start`, `yarbo plan-stop`, `yarbo plan-pause`, `yarbo plan-resume`
  - `yarbo schedules`, `yarbo manual-start`, `yarbo velocity`, `yarbo roller`, `yarbo manual-stop`
  - `yarbo global-params`, `yarbo map` — read-only data
  - All commands support `--broker` / `--sn` or auto-discover with primary/fallback
- **Discovery**
  - Auto-detect local subnets when `subnet` omitted (Linux/macOS/Windows via `ip`/`ifconfig`/`ipconfig`)
  - Skip large subnets (prefixlen &lt; 20) by default to avoid Docker /16 scans
  - Cap hosts per subnet (default 512, `--max-hosts` to increase)
  - DC classification via hostname hint (`YARBO`) when ARP gives same MAC for both IPs
  - Exactly one endpoint recommended (DC when both Rover and DC; else first)
  - `connection_order(endpoints)` — try order for primary/fallback (e.g. Home Assistant)
- **Telemetry**
  - Extra parsed fields: name, head_serial_number, battery_status, rtk_status, chute_angle, odom_confidence, car_controller, wireless_charge_*, route_priority, last_updated
  - `YarboTelemetry.all_mqtt_values()` — flattened dict of every MQTT payload key (dotted paths)
  - `flatten_mqtt_payload()` in `models` — flatten nested DeviceMSG for full visibility
  - `yarbo status` prints parsed fields plus "All MQTT keys" section
- **Security / CI**
  - pip-audit: use `pip freeze` filtered (awk) + `-r requirements-audit.txt --strict` (no `--skip-pkg`)
  - Test isolation: `monkeypatch.delenv("YARBO_MQTT_USERNAME")` in `test_default_username`
- **Local/get_map**
  - Safer handling when `get_map` or `read_global_params` response `data` is not a dict (return `{"data": data}`)

### Changed

- Discovery: `subnet` is optional; when omitted, host local networks are scanned
- Docs/README: subnet optional, connection_order and failover pattern, CLI usage

## [0.1.0] — 2026-02-26

### Added

- `YarboLocalClient` — async MQTT-only client for anonymous local control
  - `connect()` / `disconnect()` / async context manager
  - `get_controller()` — controller role handshake (required before action commands)
  - `lights_on()` / `lights_off()` / `lights_body()` — LED control
  - `set_lights(YarboLightState)` — per-channel LED control (7 channels, 0–255)
  - `buzzer(state)` — buzzer control
  - `set_chute(vel)` — snow chute direction (snow blower models)
  - `get_status()` — single telemetry snapshot
  - `watch_telemetry()` — async generator for live telemetry stream
  - `publish_raw(cmd, payload)` — escape hatch for arbitrary commands
  - `connect_sync()` — synchronous wrapper for scripts/REPL
- `YarboClient` — hybrid orchestrator (local preferred, cloud optional)
  - Delegates all local commands to `YarboLocalClient`
  - `list_robots()` / `get_latest_version()` — cloud features (lazy-initialised)
  - `YarboClient.connect()` — sync factory returning `_SyncYarboLocalClient`
- `YarboCloudClient` — async REST API client with JWT auth
  - `list_robots()` / `bind_robot()` / `unbind_robots()` / `rename_robot()`
  - `get_notification_settings()` / `get_device_messages()`
  - `get_latest_version()`
- `YarboAuth` — JWT authentication with RSA-PKCS1v15 password encryption
  - Automatic token refresh (60 seconds before expiry)
  - Falls back to full login if refresh token has expired
- `MqttTransport` — asyncio-compatible paho-mqtt wrapper
  - Bridged via `loop.call_soon_threadsafe` for thread safety
  - `telemetry_stream()` — async generator for `data_feedback` messages
- `discover_yarbo()` — local network auto-discovery
  - Probes known broker IPs + optional subnet scan
  - MQTT sniff to extract serial number from `snowbot/+/device/data_feedback`
- Data models: `YarboRobot`, `YarboTelemetry`, `YarboLightState`,
  `YarboPlan`, `YarboSchedule`, `YarboCommandResult`
- Exception hierarchy: `YarboError` → `YarboConnectionError`,
  `YarboTimeoutError`, `YarboProtocolError`, `YarboAuthError`,
  `YarboTokenExpiredError`, `YarboCommandError`, `YarboNotControllerError`
- `_codec.encode()` / `_codec.decode()` — zlib+JSON codec helpers
- `const.py` — all protocol constants (topics, ports, broker addresses)
- Full test suite (pytest + pytest-asyncio, mock MQTT, ≥70% coverage)
- CI: lint (ruff) + type-check (mypy) + test (pytest) × Python 3.11/3.12/3.13
- Release workflow: build + PyPI publish + GitHub Release
- Security workflow: weekly pip-audit + bandit scan
- Dependabot for pip and GitHub Actions
- Branch protection on `main` and `develop`

### Protocol

- Confirmed: all MQTT payloads are `zlib.compress(json.dumps(payload).encode())`
- Confirmed: `get_controller` required before `light_ctrl`, `cmd_buzzer`, etc.
- Confirmed: topics `snowbot/{SN}/app/{cmd}` and `snowbot/{SN}/device/{feedback}`
- Confirmed: light keys `led_head`, `led_left_w`, `led_right_w`, `body_left_r`,
  `body_right_r`, `tail_left_r`, `tail_right_r` (integers 0–255)
- Local broker: EMQX on port 1883 (use `yarbo discover --subnet <CIDR>` to find broker IP)

[Unreleased]: https://github.com/markus-lassfolk/python-yarbo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/markus-lassfolk/python-yarbo/releases/tag/v0.1.0
