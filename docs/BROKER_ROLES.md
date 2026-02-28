# Yarbo local broker roles (Rover vs Data Center)

IP addresses are DHCP-assigned and will vary by network. This document records one observed mapping for **reference only** — do not rely on these IPs as a source of truth in code.

## Observed mapping (example network)

IPs are DHCP-assigned; use discovery or your router’s client list to map MAC → IP.

| Role   | MAC address       | Placeholder   |
|--------|-------------------|---------------|
| Rover  | `c8:fe:0f:ff:74:56` | `<rover-ip>`  |
| DC     | `e0:4e:7a:95:a3:1d` | `<dc-ip>`     |
| DC     | `9e:cd:0a:69:9e:58` | `<dc-ip>`     |

- **Rover** — Often the robot’s WiFi interface; may drop when the robot is out of WiFi range.
- **DC (Data Center)** — HaLow base station(s); stay in place and communicate with the robot over HaLow, so telemetry can remain available when the robot is far from the router.

MAC addresses are device-stable and can be used with your router’s DHCP/ARP tables (e.g. static reservations or “client list”) to identify which discovered broker is Rover vs DC on your network.
