# LAB HANDOVER PROCEDURE & INTEGRATION RUNBOOK

**Project:** Mitsubishi Q-Series PLC Remote Monitoring System  
**Target Controller:** Mitsubishi Electric MELSEC-Q (Q03UDECPU) via MC Protocol 3E Binary (Strictly Read-Only)  
**Document Status:** Release Ready for Laboratory Integration  
**Date:** October 17, 2026

---

## 1. Required Software and Hardware

### Hardware Checklist
- [ ] **Mitsubishi Q03UDECPU PLC:** Built-in 100BASE-TX Ethernet interface configured on lab LAN (default `192.168.3.39:5010`).
- [ ] **Physical I/O Rig:** Verified contact `X3` (Pushbutton) driving master output relay `Y20` via autonomous internal ladder logic.
- [ ] **Windows Laptop / Workstation:** Ethernet port connected to PLC switch/LAN, USB port available for edge microcontroller.
- [ ] **NodeMCU ESP8266 Module:** ESP-12E board with CH340 or CP2102 USB-to-UART chip.
- [ ] **Micro-USB Cable:** Full data cable (do NOT use a power-only charging cable).
- [ ] **2.4 GHz Wi-Fi Network:** Internet-accessible Wi-Fi network for NodeMCU outbound MQTT/TLS uplink.

### Software Prerequisites
- [ ] **Python 3.11+ (Windows 64-bit):** Verified with `python --version`.
- [ ] **Arduino IDE 2.x (or CLI):** For flashing firmware to NodeMCU ESP8266.
  - ESP8266 Board Core: `ESP8266 by ESP8266 Community` (v3.1.2 or higher).
  - Arduino Libraries: `ArduinoJson` (>= 7.0.0), `PubSubClient` (>= 2.8.0).
- [ ] **Modern Web Browser:** Chrome, Edge, or Firefox supporting secure WebSockets (`wss://`).
- [ ] **HiveMQ Cloud Free Account:** For hosted MQTT broker (or on-premises Mosquitto broker).

---

## 2. Configuration Values to Confirm in the Lab

Before powering on the system, confirm and record the following lab-specific parameters:

| Parameter | Configuration Location | Expected Lab Value | Verified Offline Default |
|---|---|---|---|
| **PLC IP Address** | `gateway/config/settings.ini` (`[plc] ip`) | Laboratory LAN IP | `192.168.3.39` |
| **PLC TCP Port** | `gateway/config/settings.ini` (`[plc] port`) | MC Protocol Port | `5010` |
| **Serial COM Port** | `gateway/config/settings.ini` (`[serial] port`) | Device Manager Port | `COM9` |
| **Serial Baud Rate** | `gateway/config/settings.ini` & `plc_monitor.ino` | Matching Baud | `115200` |
| **Gateway ID** | `gateway/config/settings.ini` (`[gateway] id`) | Unique Gateway ID | `plc-gw-01` |
| **Wi-Fi SSID** | `firmware/plc_monitor/secrets.h` | Lab Wi-Fi Name | `your_wifi_ssid_here` |
| **Wi-Fi Password** | `firmware/plc_monitor/secrets.h` | Lab Wi-Fi Password | `your_wifi_password_here` |
| **MQTT Host** | `firmware/plc_monitor/secrets.h` | HiveMQ Hostname | `xxxx.s1.eu.hivemq.cloud` |
| **MQTT Port (Device)** | `firmware/plc_monitor/secrets.h` | TLS Native MQTT | `8883` |
| **MQTT Port (Browser)**| Web Interface (Settings Tab) | TLS WebSockets (WSS) | `8884` |
| **Browser Username** | Web Interface (Settings Tab) | Subscribe-only user | `browser_readonly` |
| **Topic Prefix** | Web Interface & Settings | Telemetry Root | `plcmon/plc-gw-01` |

> [!IMPORTANT]
> `gateway/config/settings.ini` maintains distinct sections: `[plc]` uses `port = 5010` (integer TCP port) while `[serial]` uses `port = COM9` (string serial device).

---

## 3. Exact Windows Commands to Install Dependencies and Run Tests

Run these commands in Windows PowerShell from the repository root (`mitsubishi-q-plc-app_ANTIGRAVITY`):

### A. Set Up Python Gateway Environment
```powershell
# Navigate to gateway directory
cd gateway

# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\activate

# Install required dependencies
pip install -r requirements.txt

# Verify installed packages
pip list
```

### B. Run Python Gateway Automated Tests
```powershell
# Run deterministic unit tests (offline, no hardware needed)
python test_unit.py
```
*Expected Result:* `All 7 Python test suites passed successfully!`

### C. Run Frontend Automated Tests
```powershell
# From repository root, start local static HTTP server
cd ..
python -m http.server 8080 --directory web

# Open browser to automated test harness:
# http://localhost:8080/test.html
```
*Expected Result:* `107 PASS / 0 FAIL / 0 SKIP (100% Pass Rate)`

---

## 4. Sequential Lab Commissioning Procedure

Execute integration strictly in the following sequential order:

```
[Step 1: PLC Smoke-Test] ──► [Step 2: Serial Port Check] ──► [Step 3: Firmware Flash]
                                                                    │
[Step 6: Web Dashboard]  ◄── [Step 5: Cloud MQTT Link]   ◄── [Step 4: Gateway Daemon]
```

### Step 1: PLC MC Protocol Connection Test
*Purpose:* Verify PLC LAN connectivity and read-only response before starting the gateway daemon.
```powershell
cd gateway
.\.venv\Scripts\activate

# Run standalone smoke test (reads settings.ini by default)
python test_plc_connection.py

# Optional: Override IP/port explicitly if lab network differs:
python test_plc_connection.py --ip 192.168.3.39 --port 5010 --polls 3
```
*Expected Success Indicator:*
```text
INFO  PASS - PLC TCP connection established
--- Poll 1/3 --------------------------------
{
  "timestamp_iso": "...",
  "inputs": {"X0": 0, ..., "X3": 0, ...},
  "outputs": {"Y20": 0, ...},
  "connected": true,
  "error": null
}
[PASS] All polls succeeded - gateway is ready to run.
```

### Step 2: Serial Port Verification
*Purpose:* Confirm Windows assigns the correct COM port to the NodeMCU.
1. Plug NodeMCU ESP8266 into laptop USB.
2. Open Windows PowerShell:
   ```powershell
   Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Description, Manufacturer
   ```
3. Note the assigned port (e.g. `COM9`). If different, update `[serial] port = COMx` in `gateway/config/settings.ini`.

### Step 3: Flash NodeMCU Firmware
*Purpose:* Program the serial-to-MQTT bridge on the ESP8266.
> [!WARNING]
> Ensure `gateway.py` is **NOT** running before flashing, as COM9 cannot be shared.
1. Copy `firmware/plc_monitor/secrets.h.example` to `firmware/plc_monitor/secrets.h`.
2. Fill in Wi-Fi SSID, Wi-Fi password, HiveMQ hostname, and device credentials (`esp8266_device`).
3. Open `firmware/plc_monitor/plc_monitor.ino` in Arduino IDE.
4. Select Board: `NodeMCU 1.0 (ESP-12E Module)`, Port: `COM9`, Upload Speed: `115200`.
5. Click **Upload** (Ctrl+U).
6. Open Serial Monitor at 115200 baud to observe bootup:
   ```text
   [SYS] PLC Monitor firmware starting…
   [WiFi] Connected, IP: 192.168.x.x
   [MQTT] Connecting to xxxx.s1.eu.hivemq.cloud:8883
   [MQTT] Connected
   ```

### Step 4: Start Production Gateway Daemon
*Purpose:* Begin continuous read-only polling of the PLC and streaming over USB serial.
```powershell
cd gateway
.\.venv\Scripts\activate
python gateway.py
```
*Expected Success Indicator:*
```text
INFO  ═══ Gateway starting  id=plc-gw-01 ═══
INFO  PLC target: 192.168.3.39:5010  poll=1.0s
INFO  Serial: COM9 @ 115200 baud
INFO  PLC TCP socket connected
```
The NodeMCU onboard LED will blink as telemetry JSON lines are forwarded to MQTT.

### Step 5: Verify Cloud MQTT Topics
*Purpose:* Verify HiveMQ Cloud broker receives telemetry.
1. Log into [HiveMQ Cloud Web Client](https://console.hivemq.cloud).
2. Connect using browser read-only credentials.
3. Subscribe to topic `plcmon/plc-gw-01/#`.
4. Confirm incoming JSON frames on `plcmon/plc-gw-01/telemetry` every 1 second and retained status on `plcmon/plc-gw-01/status`.

### Step 6: Connect Supervisory Web Dashboard
*Purpose:* Verify end-to-end telemetry presentation.
1. Open the dashboard locally (`http://localhost:8080/index.html`) or at your GitHub Pages URL.
2. In the **Settings** tab:
   - Enter Broker WSS URL: `wss://xxxx.s1.eu.hivemq.cloud:8884/mqtt`
   - Enter Read-Only Username: `browser_readonly`
   - Enter Password.
   - Confirm Topic Prefix: `plcmon/plc-gw-01`.
   - Ensure **Offline Demonstration Mode** is UNCHECKED.
3. Click **Connect to Broker**.
4. Check **Overview** and **PLC I/O Monitor** tabs:
   - Header pills: `BROKER: Connected`, `BRIDGE: Online`, `GATEWAY: Streaming`, `PLC: Connected`.
   - Freshness: `LIVE (<1s ago)`.
   - Press physical pushbutton `X3` on the lab rig: verify tile `X3` and output `Y20` energize (green LED / closed rung contact).
   - Release `X3`: verify `X3` and `Y20` de-energize.

---

## 5. Expected Success Indicators at Every Step

| Commissioning Step | Terminal / Interface Indicator | Physical Hardware Indicator |
|---|---|---|
| **Step 1: Smoke-Test** | `[PASS] All polls succeeded` | PLC ERR LED remains OFF; SD/RD Ethernet LEDs flash |
| **Step 2: Serial Check** | `DeviceID: COM9, USB-SERIAL CH340` | Device Manager shows active COM port |
| **Step 3: Firmware Flash** | `[WiFi] Connected`, `[MQTT] Connected` | NodeMCU blue onboard LED flashes once |
| **Step 4: Gateway Startup**| `Gateway starting`, `PLC TCP connected` | NodeMCU RX LED pulses rhythmically every 1s |
| **Step 5: Cloud Ingestion**| Payload visible on `plcmon/.../telemetry` | HiveMQ console message throughput increments |
| **Step 6: Supervisory HMI**| Header dots turn GREEN, Freshness reads `LIVE` | Pressing `X3` lights UI contact and relay `Y20` |

---

## 6. Recovery Steps for Common Connection Failures

### 1. `PLC connect failed: timed out` or `Connection refused`
- **Cause:** Incorrect PLC IP/port, Ethernet cable unplugged, or laptop IP on wrong subnet.
- **Troubleshooting:**
  1. Ping the PLC: `ping 192.168.3.39`.
  2. Verify laptop Ethernet adapter is set to static IP in the same subnet (e.g. `192.168.3.100`, subnet mask `255.255.255.0`).
  3. Confirm PLC CPU is in `RUN` mode.
  4. In GX Works2: Verify PLC Parameters → Built-in Ethernet Port Settings → MC Protocol is enabled on TCP port 5010.

### 2. `Serial port unavailable – will retry in loop`
- **Cause:** COM port mismatch, loose USB cable, or Arduino Serial Monitor is holding the port.
- **Troubleshooting:**
  1. Close Arduino Serial Monitor and PuTTY.
  2. Check Device Manager to verify COM port number.
  3. Unplug and replug the NodeMCU USB cable.

### 3. ESP8266 `[WiFi] Timeout – will retry`
- **Cause:** 2.4 GHz Wi-Fi credentials incorrect or weak Wi-Fi signal.
- **Troubleshooting:**
  1. Verify SSID and password in `firmware/plc_monitor/secrets.h`.
  2. Ensure the access point supports 802.11 b/g/n (ESP8266 does NOT support 5 GHz networks).

### 4. ESP8266 `[MQTT] Failed, rc=5` or `rc=-2`
- **Cause:** `rc=5`: Authentication rejected. `rc=-2`: Cannot reach broker host or port blocked.
- **Troubleshooting:**
  1. Check HiveMQ cluster hostname in `secrets.h`.
  2. Verify `esp8266_device` username and password under HiveMQ Cloud Access Management.
  3. Ensure port `8883` is not blocked by local network firewall.

### 5. Browser HMI Displays `Broker ACL Rejection (QoS 128)`
- **Cause:** The credentials used in the browser do not have permission to subscribe to `plcmon/plc-gw-01/#`.
- **Troubleshooting:**
  1. In HiveMQ Cloud Access Management, edit user `browser_readonly`.
  2. Grant **Subscribe** permission to `plcmon/#`.
  3. Ensure publish permission is NOT granted.

---

## 7. Boundary: Verified Offline Behavior vs Unverified Live Behavior

To maintain strict engineering integrity, observe the distinction between what has been deterministically verified offline versus what remains blocked pending live lab access:

| System Subsystem | Deterministically Verified Offline | Unverified Live Laboratory Behavior |
|---|---|---|
| **PLC Communication** | Address translation logic, dataclass serialisation, 3E socket configuration schema, timeout handling. | Physical TCP socket negotiation with Q03UDECPU, hardware scan cycle latency. |
| **Serial Gateway** | Newline-delimited JSON framing, sequence monotonicity, heartbeat generator, error handling. | Windows serial driver hardware performance on COM9 with CH340 chip. |
| **NodeMCU Firmware** | Arduino sketch syntax, PubSubClient topic definitions, non-blocking Wi-Fi loop, JSON parsing. | Physical Wi-Fi RF handshake, ESP8266 TLS 1.2 certificate exchange with HiveMQ. |
| **Supervisory Frontend** | ISA-101 5-tab HMI, dark/light themes, offline demo simulation with 5 fault scenarios, static ladder rung continuity, CSV export, duplicate/out-of-order/retained packet rejection, mobile layout. | Live WebSocket throughput through internet proxies and mobile carrier networks. |
| **Control Safety** | Strictly read-only codebase (no `0x1401` PLC write commands, no MQTT write topics, no output forcing). | N/A (Guaranteed by static architecture). |

---

## Document Sign-Off

- **Stage:** 1.9 Offline Deployment Readiness & Lab Preparation  
- **Feature Development:** **FROZEN**  
- **Software Readiness:** **100% READY FOR HARDWARE INTEGRATION**
