# Yarbo local broker roles (Rover vs Data Center)

IP addresses are DHCP-assigned and will vary by network. Use discovery (`yarbo discover`) or your router's client list to identify brokers.

## Roles

| Role   | Description | Placeholder |
|--------|-------------|-------------|
| Rover  | The robot's WiFi interface; may drop when the robot is out of WiFi range. | `<rover-ip>` |
| DC     | HaLow base station(s); stay in place and communicate with the robot over HaLow, so telemetry can remain available when the robot is far from the router. | `<dc-ip>` |

## Identifying your brokers

1. Run `yarbo discover` — it scans your local subnet and classifies each broker as Rover or DC.
2. Alternatively, check your router's DHCP/ARP client list for devices with a Yarbo MAC prefix (`C8:FE:0F:*`).
3. The DC hostname often starts with `YARBO` in the DHCP lease table.

MAC addresses are device-stable and can be used for static DHCP reservations to ensure consistent IPs.
