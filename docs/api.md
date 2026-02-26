# API Reference

## `yarbo.YarboClient`

Hybrid client — prefers local MQTT, falls back to cloud for account features.

```python
YarboClient(
    broker: str = "192.168.1.24",
    sn: str = "",
    port: int = 1883,
    username: str = "",          # Cloud only
    password: str = "",          # Cloud only
    rsa_key_path: str | None = None,
    auto_controller: bool = True,
)
```

### Local Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `await connect()` | `None` | Connect to MQTT broker |
| `await disconnect()` | `None` | Disconnect |
| `await get_controller()` | `YarboCommandResult \| None` | Acquire controller role |
| `await get_status(timeout)` | `YarboTelemetry \| None` | Single telemetry snapshot |
| `watch_telemetry()` | `AsyncIterator[YarboTelemetry]` | Live telemetry stream |
| `await lights_on()` | `None` | All LEDs → 255 |
| `await lights_off()` | `None` | All LEDs → 0 |
| `await set_lights(state)` | `None` | Per-channel LED control |
| `await buzzer(state=1)` | `None` | Buzzer on/off |
| `await set_chute(vel)` | `None` | Snow chute direction |
| `await publish_raw(cmd, payload)` | `None` | Arbitrary MQTT command |

### Cloud Methods (require username/password)

| Method | Returns | Description |
|--------|---------|-------------|
| `await list_robots()` | `list[YarboRobot]` | All bound robots |
| `await get_latest_version()` | `dict` | App/firmware/DC versions |

### Sync Factory

```python
client = YarboClient.connect(broker="192.168.1.24", sn="...")
client.lights_on()
client.disconnect()
```

---

## `yarbo.YarboLocalClient`

MQTT-only, anonymous local control. Same interface as `YarboClient` (local methods only).

---

## `yarbo.YarboCloudClient`

Async REST API client with JWT auth.

```python
async with YarboCloudClient(username="u@example.com", password="secret") as client:
    robots = await client.list_robots()
```

---

## `yarbo.YarboLightState`

```python
@dataclass
class YarboLightState:
    led_head: int = 0       # Front white (0–255)
    led_left_w: int = 0     # Left fill white
    led_right_w: int = 0    # Right fill white
    body_left_r: int = 0    # Left body red
    body_right_r: int = 0   # Right body red
    tail_left_r: int = 0    # Left tail red
    tail_right_r: int = 0   # Right tail red

    # Factories
    @classmethod def all_on() -> YarboLightState: ...
    @classmethod def all_off() -> YarboLightState: ...
    @classmethod def from_dict(d: dict) -> YarboLightState: ...
    def to_dict() -> dict[str, int]: ...
```

---

## `yarbo.YarboTelemetry`

Parsed `data_feedback` message from the robot (delivered at ~1 Hz).

```python
@dataclass
class YarboTelemetry:
    sn: str
    battery: int | None       # 0–100 %
    state: str | None         # "idle", "working", "charging", ...
    error_code: str | None    # Active fault code
    position_x: float | None  # Metres (local frame)
    position_y: float | None
    heading: float | None     # Degrees 0–360
    speed: float | None       # m/s
    led: int | None           # Raw hardware LED register
    raw: dict                 # Complete DeviceMSG payload
```

---

## `yarbo.discover_yarbo`

```python
robots = await discover_yarbo(
    timeout: float = 5.0,
    port: int = 1883,
    subnet: str | None = None,   # e.g. "192.168.1.0/24"
) -> list[DiscoveredRobot]
```

```python
@dataclass
class DiscoveredRobot:
    broker_host: str
    broker_port: int
    sn: str = ""
```

---

## Exception Hierarchy

```
YarboError
├── YarboConnectionError       # Network / MQTT connection failed
│   └── YarboTimeoutError      # Timed out
├── YarboProtocolError         # Malformed payload
├── YarboAuthError             # 401 / 403 / bad credentials
│   └── YarboTokenExpiredError # JWT expired
└── YarboCommandError          # Robot rejected command
    └── YarboNotControllerError # Need get_controller first
```

---

## `yarbo._codec`

```python
from yarbo._codec import encode, decode

encoded: bytes = encode({"led_head": 255})   # zlib(JSON)
decoded: dict  = decode(encoded)             # → {"led_head": 255}
```
