# Changelog

All notable changes to python-yarbo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

---

## [2026.3.11] — 2026-03-01

### Added

- **56 typed command methods** — full coverage of all Yarbo MQTT commands
  - Camera: `check_camera_status`, `camera_calibration`
  - Firmware: `firmware_update_now`, `firmware_update_tonight`, `firmware_update_later`
  - Diagnostics: `get_battery_cell_temps`, `get_motor_temps`, `get_body_current`, `get_head_current`, `get_odometer`, `get_speed`, `get_product_code`
  - WiFi: `get_connected_wifi`, `get_wifi_list`, `get_saved_wifi_list`, `start_hotspot`
  - Plans: `read_plan`, `read_all_plans`, `read_schedules`, `read_no_charge_period`, `read_recharge_point`, `save_charging_point`
  - Settings: `set_auto_update`, `set_camera`, `set_camera_ota`, `set_child_lock`, `set_draw_mode`, `set_edge_blowing`, `set_elec_fence`, `set_follow_mode`, `set_geo_fence`, `set_heating_film`, `set_module_lock`, `set_motor_protect`, `set_ngz_edge`, `set_smart_blowing`, `set_smart_vision`, `set_trimmer`, `set_usb`, `set_video_record`
  - Maps: `save_current_map`, `save_map_backup`, `get_all_map_backup`, `map_recovery`, `erase_map`
  - Bag record: `bag_record` (with destructive confirm safeguard)
- **Head-type validation** — `_validate_head_type()` checks attached head before blade/snow/roller commands; raises `ValueError` on mismatch
- **Destructive operation safeguards** — `delete_plan`, `delete_all_plans`, `erase_map`, `bag_record` require `confirm=True`
- **Default error reporting** (beta) — built-in GlitchTip DSN, opt-out via `YARBO_SENTRY_DSN=""`
- **MQTT debug and capture** — CLI supports `--debug`, `--debug-raw`, `--mqtt-log-path`, `--mqtt-capture-max`
- **`_maybe_report_mqtt`** — CLI helper to send captured MQTT dumps to GlitchTip

### Fixed

- **TLS security** — `tls_set_context(ssl.create_default_context())` used when no CA file specified; `CERT_NONE` never used as default
- **Event loop safety** — `call_soon_threadsafe` guarded against closed event loops in MQTT callbacks
- **Wildcard MQTT subscription** — discovery mode works without serial number
- **Breadcrumb scrubbing** — passwords, tokens, API keys redacted in Sentry breadcrumbs
- **`publish_command` delegation** — now correctly delegates to `publish_command` (not `publish_raw`)
- **Duplicate `_DEFAULT_DSN`** removed; opt-out documentation corrected

### Changed

- **Roller speed payload** harmonised to `{speed: N}` format
- **`set_chute_steering_work`** parameter renamed to `state` (from `angle`)
- **`set_sound_param`** differentiated from `set_sound` with variant documentation

---

## [2026.3.10] — 2026-03-01

First public release. Local MQTT control only — cloud integration is experimental and not fully tested.

### Added

- **CLI (`yarbo`)** — full-featured command-line interface
  - `yarbo discover` — find Rover/DC brokers (optional `--subnet`, `--max-hosts`)
  - `yarbo status` — connect with primary/fallback, print full telemetry
  - `yarbo battery`, `yarbo telemetry` — status and live stream
  - `yarbo lights-on`, `yarbo lights-off`, `yarbo buzzer`, `yarbo chute`, `yarbo return-to-dock`
  - `yarbo plans`, `yarbo plan-start`, `yarbo plan-stop`, `yarbo plan-pause`, `yarbo plan-resume`
  - `yarbo schedules`, `yarbo manual-start`, `yarbo velocity`, `yarbo roller`, `yarbo manual-stop`
  - `yarbo global-params`, `yarbo map` — read-only data
  - All commands support `--broker` / `--sn` or auto-discover
- **Discovery** — auto-detect local subnets, skip large ranges, DC/Rover classification
- **Telemetry** — full MQTT telemetry parsing with 40+ fields
- **25+ typed command methods** with full docstrings and parameter validation
  - Mowing: `start_plan`, `stop_plan`, `pause_plan`, `resume_plan`
  - Configuration: `set_velocity`, `set_lights`, `set_person_detect`, `set_ignore_obstacles`
  - Snow: `push_snow_dir`, `set_chute`, `set_chute_steering_work`
  - Maintenance: `firmware_update_now/tonight/later`, `check_camera_status`, `camera_calibration`
  - Head-specific: `set_roller_speed`, `set_blade_height`, `set_blade_speed`
  - Data: `get_map`, `get_saved_wifi_list`, `get_status`, `get_global_params`
- **Destructive operation safeguards** — `delete_plan`, `delete_all_plans`, `erase_map`, `map_recovery` require `confirm=True`
- **Head-type validation** — commands validated against current head type (mower/snow blower/sweeper)
- **Debug logging** — optional verbose MQTT logging for troubleshooting

### Security

- **TLS certificate validation enforced** for cloud MQTT connections (system trust store by default)
- Discovery future race condition fixed (multiple heartbeats no longer cause `InvalidStateError`)
- `YarboAuth` now implements context manager for proper session lifecycle (`async with`)
- Logout failures logged instead of silently swallowed

### Note

> **Cloud integration is experimental.** The cloud MQTT and HTTP API modules (`cloud_mqtt.py`, `cloud.py`, `auth.py`) are included for completeness but have not been tested against live Yarbo cloud infrastructure. Use local MQTT control for production. Cloud support will be fully validated in a future release.


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
