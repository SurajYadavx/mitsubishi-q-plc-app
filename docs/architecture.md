# Architecture

## System Overview

```
Mitsubishi Q03UDECPU PLC (192.168.3.39:5010)
        │
        │  MC Protocol / TCP (private LAN — never exposed to internet)
        ▼
Windows Laptop Gateway  (Python 3.11 + pymcprotocol)
        │
        │  USB Serial — newline-delimited JSON — 115200 baud  (COM9)
        ▼
NodeMCU ESP8266 (Arduino firmware)
        │
        │  Wi-Fi → MQTT/TLS (port 8883)
        ▼
Managed MQTT Broker  (EMQX Cloud Serverless or equivalent)
        │
        │  MQTT WebSockets / WSS (port 8084, path /mqtt)
        ▼
GitHub Pages Static Frontend  (MQTT.js over WSS)
        │
        └─ Browser on laptop / tablet / phone
```

## Design Constraints

| Constraint | Reason |
|---|---|
| Laptop gateway is required to be running | GitHub Pages cannot run Python; the PLC LAN is not internet-routable |
| ESP8266 connected via USB serial only | No Ethernet module available; Wi-Fi is the only upstream path |
| MQTT broker in the cloud | Only outbound connections from the laptop and ESP8266 are possible; no port forwarding required |
| Frontend is read-only | First release is strictly monitoring; no output forcing |

## Topic Structure

```
plcmon/{gateway_id}/telemetry   — PLC readings (JSON, not retained)
plcmon/{gateway_id}/status      — Device availability (retained LWT)
plcmon/{gateway_id}/diag        — Gateway diagnostics / errors
```

Default `gateway_id`: `plc-gw-01`

## Telemetry JSON Schema

### plc_reading
```json
{
  "type":      "plc_reading",
  "gw_id":     "plc-gw-01",
  "seq":       12345,
  "ts":        "2025-01-01T12:00:00Z",
  "connected": true,
  "error":     null,
  "inputs":  { "X0":0,"X1":0,"X2":0,"X3":1,"X4":0,"X5":0,"X6":0,"X7":0 },
  "outputs": { "Y20":1,"Y21":0,"Y22":0,"Y23":0,"Y24":0,"Y25":0,"Y26":0,"Y27":0 }
}
```

### gateway_error
```json
{
  "type":      "gateway_error",
  "gw_id":     "plc-gw-01",
  "seq":       12346,
  "ts":        "2025-01-01T12:00:05Z",
  "connected": false,
  "error":     "Connection refused"
}
```

### heartbeat
```json
{
  "type":      "heartbeat",
  "gw_id":     "plc-gw-01",
  "seq":       12347,
  "ts":        "2025-01-01T12:00:10Z",
  "connected": true,
  "error":     null
}
```

### status (MQTT retained, ESP8266 → broker)
```json
{ "status": "online",  "device": "nodemcu-plc-01" }
{ "status": "offline", "device": "nodemcu-plc-01" }
```

## Security Model

| Layer | Mechanism |
|---|---|
| PLC LAN | Private network; PLC IP/port never exposed to internet |
| Serial transport | Physical USB cable; no network exposure |
| MQTT broker auth | Username + password per client; TLS in transit |
| ESP8266 credentials | `secrets.h` excluded from Git via `.gitignore` |
| Browser credentials | Read-only MQTT user; separate from device publisher credentials |
| Frontend | No PLC address, no write topics, no admin credentials |

> **Note:** Browser credentials (WSS username/password) are visible to JavaScript
> and therefore not truly secret. Use a dedicated, permission-restricted read-only
> MQTT account. Document this exposure clearly.

## Device Mapping

| PLC Device | Type | Hexadecimal Addr | Decimal Addr | Notes |
|---|---|---|---|---|
| X0–X7 | Digital input | 0x00–0x07 | 0–7 | Physical inputs (Q-Series Hex addressing) |
| X3 | Digital input | 0x03 | 3 | Controls Y20 in existing ladder |
| Y20–Y27 | Digital output | 0x20–0x27 | 32–39 | Y20 controlled by X3 ladder rung |

> **Y21–Y27 usage is unverified.** Inspect in GX Works2 before any future use.

## Existing Ladder Logic

The Q03UDECPU contains a rung: **X3 → Y20** (contact X3 drives coil Y20).

This system:
- Never writes to Y20 or any other output.
- Never forces any coil.
- Never downloads programs.
- Reads Y20 state only, to reflect the actual coil state in the dashboard.
