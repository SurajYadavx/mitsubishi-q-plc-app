# ESP8266 Firmware Setup

## Hardware

| Item | Detail |
|---|---|
| Board | NodeMCU 1.0 (ESP-12E) |
| MCU | ESP8266 |
| USB driver | CH340 (install from manufacturer if Windows does not detect COM port) |
| USB cable | Data cable (not charge-only) |
| Serial port | COM9 @ 115200 baud (same port used for flashing and gateway serial) |

> **Important:** The gateway and Arduino IDE Serial Monitor **cannot share COM9 simultaneously**.
> Stop the gateway before flashing. Restart the gateway after flashing.

## Arduino IDE Setup

### 1. Install Arduino IDE 2.x

Download from https://www.arduino.cc/en/software

### 2. Add ESP8266 board support

In Arduino IDE: File → Preferences → Additional boards manager URLs, add:

```
https://arduino.esp8266.com/stable/package_esp8266com_index.json
```

Then: Tools → Board → Boards Manager → search "esp8266" → install **ESP8266 by ESP8266 Community** (version ≥ 3.1.2)

### 3. Install required libraries

Tools → Manage Libraries, search and install:

| Library | Author | Version |
|---|---|---|
| ArduinoJson | Benoit Blanchon | ≥ 7.0.0 |
| PubSubClient | Nick O'Leary | ≥ 2.8.0 |

### 4. Board settings

Tools menu:
- **Board:** NodeMCU 1.0 (ESP-12E Module)
- **Upload Speed:** 115200
- **CPU Frequency:** 80 MHz
- **Flash Size:** 4MB (FS: 2MB, OTA: ~1019KB)
- **Port:** COM9

## Credentials

1. Navigate to `firmware/plc_monitor/`
2. Copy `secrets.h.example` → `secrets.h`
3. Fill in your values:

```cpp
#define WIFI_SSID       "YourNetworkName"
#define WIFI_PASSWORD   "YourWiFiPassword"
#define MQTT_HOST       "xxxx.s1.eu.hivemq.cloud"
#define MQTT_PORT       8883
#define MQTT_USER       "esp8266_device_user"
#define MQTT_PASS       "your_device_password"
#define MQTT_CLIENT_ID  "nodemcu-plc-01"
```

`secrets.h` is in `.gitignore` and must never be committed.

## HiveMQ Cloud Broker Setup

1. Go to https://console.hivemq.cloud and create a free account
2. Create a new cluster (free tier: 100 connections, 10 GB/month)
3. Note the **Hostname**, e.g. `abc123.s1.eu.hivemq.cloud`
4. Under **Access Management**, create two credentials:

| Username | Role | Used by |
|---|---|---|
| `esp8266_device_user` | Publish to `plcmon/#` | ESP8266 firmware |
| `browser_readonly` | Subscribe to `plcmon/#` only | GitHub Pages frontend |

5. HiveMQ Cloud enables TLS by default:
   - ESP8266 → port **8883** (MQTT/TLS)
   - Browser → port **8884** (MQTT over WebSocket/TLS)

> **Note on `setInsecure()`:** The current firmware uses `wifiClient.setInsecure()` which
> skips TLS certificate verification. This is acceptable for initial testing on a trusted
> network but should be upgraded to `setCACert()` with HiveMQ's CA certificate for
> production use. See the TODO comment in `plc_monitor.ino`.

## Flashing

1. Stop the gateway if running
2. Open `firmware/plc_monitor/plc_monitor.ino` in Arduino IDE
3. Verify `secrets.h` exists and is filled in
4. Click **Upload** (Ctrl+U)
5. Wait for "Done uploading"
6. Open Serial Monitor (115200 baud) to observe startup messages:

```
[SYS] PLC Monitor firmware starting…
[WiFi] Connecting…
[WiFi] Connected, IP: 192.168.1.x
[MQTT] Connecting to abc123.s1.eu.hivemq.cloud:8883
[MQTT] Connected
```

7. Reconnect the gateway — JSON lines will appear in the Serial Monitor and the
   ESP8266 will forward them to MQTT.

## Wiring / Connections

No external wiring is required.  The NodeMCU receives power and data via the USB cable.

```
Windows laptop USB port
      │
      │  USB (CH340 chip on NodeMCU)
      │
NodeMCU RX (GPIO3) ←── data from laptop serial TX
NodeMCU TX (GPIO1) ──→ data to laptop serial RX
```

The USB serial is simultaneously used for:
- Flashing firmware (when the gateway is stopped)
- Gateway serial JSON transport (during normal operation)
- Arduino Serial Monitor (for debugging, when gateway is stopped)

## Topic mapping

The firmware publishes to:

| Topic | Content |
|---|---|
| `plcmon/plc-gw-01/telemetry` | Validated PLC readings (pass-through from gateway JSON) |
| `plcmon/plc-gw-01/status` | `{"status":"online","device":"nodemcu-plc-01"}` (retained) |
| `plcmon/plc-gw-01/diag` | Diagnostic messages |

The Last-Will message (`{"status":"offline","device":"nodemcu-plc-01"}`) is published automatically to `plcmon/plc-gw-01/status` if the MQTT connection drops unexpectedly.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| COM port not found | CH340 driver missing | Install from https://www.wch.cn/downloads/CH341SER_EXE.html |
| Upload fails | Gateway is using COM9 | Stop gateway, retry |
| `[WiFi] Timeout` | Wrong SSID/password | Check secrets.h |
| `[MQTT] Failed, rc=-2` | Wrong host/port | Check broker hostname and port 8883 |
| `[MQTT] Failed, rc=5` | Auth rejected | Check username/password in broker console |
| JSON not forwarded | Gateway not running | Start gateway, verify JSON on serial |
