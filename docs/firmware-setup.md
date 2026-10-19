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
#define MQTT_HOST       "xxxxxx.emqxsl.com"
#define MQTT_PORT       8883
#define MQTT_USER       "plc_monitor_device"
#define MQTT_PASS       "your_device_password"
#define MQTT_CLIENT_ID  "nodemcu-plc-01"
```

`secrets.h` is in `.gitignore` and must never be committed.

## EMQX Cloud Serverless Broker Setup

1. Go to https://cloud.emqx.com and create an EMQX Cloud deployment
2. Copy the deployment **Hostname**, e.g. `xxxxxx.emqxsl.com`
3. Under **Authentication → Password-Based**, create credentials:

| Username | Role | Used by |
|---|---|---|
| `plc_monitor_device` | Publish to `plcmon/#` | ESP8266 firmware |
| `browser_readonly` | Subscribe to `plcmon/#` only | GitHub Pages frontend |

4. Under **Authorization (ACL)**, enforce topic permissions:
   - `plc_monitor_device`: Allow Publish on `plcmon/#`, Deny Subscribe.
   - `browser_readonly`: Allow Subscribe on `plcmon/#`, Deny Publish.

5. EMQX Cloud Serverless ports:
   - ESP8266 → port **8883** (MQTT over TLS)
   - Browser → port **8084** (Secure WebSockets WSS with path `/mqtt`)

> **Note on `setInsecure()`:** The firmware currently uses `wifiClient.setInsecure()` which
> enables TLS 1.2 traffic encryption to EMQX port 8883 while skipping root CA certificate
> verification. This prevents ESP8266 heap memory exhaustion during the handshake.
> For strict certificate pinning in high-security environments, the downloaded EMQX root CA
> certificate can be loaded via `setTrustAnchor(&cert)`.

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
[MQTT] Connecting to abc123.emqxsl.com:8883
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
