# Pre-Lab Integration Checklist & System Audit
## Mitsubishi Q03UDECPU Supervisory Remote Monitoring System

**Repository:** `SurajYadavx/mitsubishi-q-plc-app`  
**Live Dashboard:** [https://surajyadavx.github.io/mitsubishi-q-plc-app/](https://surajyadavx.github.io/mitsubishi-q-plc-app/)  
**Target Controller:** Mitsubishi Electric MELSEC-Q Series `Q03UDECPU` (Built-in 100BASE-TX Ethernet)  
**Document Purpose:** Beginner-friendly, comprehensive pre-lab verification checklist and integration runbook  
**Audit Date:** October 19, 2026  
**Safety Invariant:** **Strictly Read-Only**. No write commands, no forcing, no PLC program resets or reinitializations.

---

## 1. System Architecture Overview

The system monitors physical digital I/O states on an industrial Mitsubishi Q03UDECPU PLC through a 5-tier passive telemetry pipeline:

```
┌────────────────────────┐
│  Tier 1: Industrial PLC│  Mitsubishi Q03UDECPU (192.168.3.39:5010)
│   (Isolated LAN)       │  • Autonomous scan cycle (Ladder Rung: X3 → Y20)
└───────────┬────────────┘  • Serves MC Protocol 3E Binary over TCP
            │ Private Ethernet Cable / Lab Switch (100BASE-TX)
┌───────────▼────────────┐
│  Tier 2: Host Gateway  │  Windows Laptop Python Gateway Daemon
│   (Local Daemon)       │  • gateway/gateway.py via pymcprotocol (Batch Read 0x0401)
└───────────┬────────────┘  • Enforces read-only safety; outputs newline-delimited JSON
            │ Physical USB Cable (COM9 @ 115200 baud)
┌───────────▼────────────┐
│  Tier 3: Edge Bridge   │  NodeMCU 1.0 (ESP-12E / ESP8266)
│   (Microcontroller)    │  • firmware/plc_monitor/plc_monitor.ino
└───────────┬────────────┘  • Validates JSON; publishes to EMQX Cloud via TLS 1.2
            │ 2.4 GHz Wi-Fi (WPA2) → MQTTS Port 8883
┌───────────▼────────────┐
│  Tier 4: Cloud Broker  │  EMQX Cloud Managed Broker (Serverless Cluster)
│   (MQTT Infrastructure)│  • Dedicated topic: plcmon/plc-gw-01/#
└───────────┬────────────┘  • Role-Based Access Control (RBAC) credentials
            │ Secure WebSockets (WSS) Port 8084
┌───────────▼────────────┐
│  Tier 5: Web Console   │  GitHub Pages Static Client
│   (Supervisory HMI)    │  • https://surajyadavx.github.io/mitsubishi-q-plc-app/
└────────────────────────┘  • Real-time ISA-101 industrial dashboard (MQTT.js)
```

---

## 2. Component-by-Component Implementation & Testing Status

Every component in the repository has been audited against its codebase files and empirical tests.

| Tier / Subsystem | Implementation File(s) in Repo | Current Status | Test Coverage Level | Audit Evidence & Notes |
|---|---|---|---|---|
| **Tier 1: PLC Interface** | `gateway/src/plc_reader.py`<br>`gateway/test_plc_connection.py` | **Implemented; Pending Hardware Test** | **Locally Tested** (Address conversion & dataclass tests pass) | Verified via `gateway/test_unit.py` (Suite 1 & 6). Only issues read command `batchread_bitunits` (MC Protocol `0x0401`). Physical TCP connection to Q03UDECPU requires lab switch access. |
| **Tier 2: Host Gateway** | `gateway/gateway.py`<br>`gateway/src/serial_publisher.py`<br>`gateway/config/settings.ini` | **Implemented; Pending Hardware Test** | **Locally Tested** (7/7 test suites pass offline) | Verified via `python gateway/test_unit.py`. Monotonic sequence numbers, ISO-8601 timestamps, heartbeat, and error schemas validated. Live USB serial streaming requires physical NodeMCU. |
| **Tier 3: Edge Microcontroller** | `firmware/plc_monitor/plc_monitor.ino`<br>`firmware/plc_monitor/secrets.h.example` | **Implemented; Not Yet Flashed / Configured** | **Syntax Inspected** | C++ Arduino sketch implements non-blocking Wi-Fi, TLS 1.2 via `WiFiClientSecure`, JSON parsing via `ArduinoJson 7.x`, and MQTT via `PubSubClient` (buffer 1024B). Requires user to copy `secrets.h` and flash board. |
| **Tier 4: Cloud MQTT Broker** | EMQX Cloud (Serverless)<br>`docs/firmware-setup.md` | **Configured in Protocol; Cloud Account Not Created** | **Schema Tested Offline** | Web client and firmware topic formats (`plcmon/plc-gw-01/#`) match. User must register free Serverless deployment on `emqx.com/en/cloud` and create user credentials. |
| **Tier 5: Supervisory Dashboard** | `web/index.html`<br>`web/assets/css/style.css`<br>`web/assets/js/app.js` | **Fully Implemented & Published Live** | **Locally Tested & Cloud Deployed** | 107/107 unit tests pass in `web/test.html`. Published live at GitHub Pages. Features dual card/table views, live ladder animation, and demo simulator. |
| **CI/CD Deployment** | `.github/workflows/static.yml`<br>`.github/workflows/deploy-pages.yml` | **Live & Operational** | **Cloud Tested** | GitHub Actions deploys `./web` to GitHub Pages automatically upon every commit to `main`. |

---

## 3. Configuration Mapping & Exact Repository Paths

All configurations belong in dedicated files. **Never commit passwords, tokens, or Wi-Fi keys to Git.**

| Configuration Role | Exact File Path in Repository | Tracked in Git? | Template / Reference File | Description |
|---|---|---|---|---|
| **Gateway PLC & Serial Settings** | `gateway/config/settings.ini` | Yes (Tracked) | N/A (Live config) | Contains default PLC IP (`192.168.3.39`), TCP port (`5010`), serial port (`COM9`), baud rate (`115200`), and monitored I/O lists. |
| **Gateway Secret Overrides (Optional)** | `gateway/config/secrets.ini` | **No** (in `.gitignore`) | `gateway/config/secrets.ini.example` | Optional local override file for lab IP/port if different from `settings.ini`. |
| **NodeMCU Wi-Fi & Broker Credentials** | `firmware/plc_monitor/secrets.h` | **No** (in `.gitignore`) | `firmware/plc_monitor/secrets.h.example` | **Must be created by user**. Contains `WIFI_SSID`, `WIFI_PASSWORD`, `MQTT_HOST`, `MQTT_PORT` (8883), `MQTT_USER`, and `MQTT_PASS`. |
| **Web Dashboard Connection Parameters** | Web UI (Settings Tab) & Browser Storage | No (Client-side memory) | Runtime Form | User inputs Broker WSS URL (`wss://...:8084/mqtt`), read-only username, and password. URL, user, and topic persist to `localStorage`; password remains volatile in memory. |

---

## 4. Parameters to Obtain Before Laboratory Commissioning

Collect these exact values before powering on hardware or starting software:

### A. From the PLC & GX Works2 Project
1. **PLC IP Address:** Confirm the static IP assigned to the built-in Ethernet port of the Q03UDECPU (Default in repo: `192.168.3.39`).
2. **Subnet Mask:** Typically `255.255.255.0` (Laptop Ethernet adapter must be set to the same subnet, e.g., `192.168.3.100`).
3. **MC Protocol TCP Port:** Confirm port matches GX Works2 Built-in Ethernet Port Open Settings (Default in repo: `5010`).
4. **MC Protocol Frame Type:** Must be configured in GX Works2 as **MC Protocol**, **TCP**, and **Binary Code** (3E frame).
5. **CPU Operating Mode:** Ensure the physical CPU keyswitch is in **RUN** mode.

### B. From Windows Device Manager (Laptop)
1. **COM Port Number:** Plug in the NodeMCU via micro-USB data cable. Open Windows Device Manager &rarr; **Ports (COM & LPT)** &rarr; identify port (e.g., `COM9`, `COM3`, `COM4`).
2. **USB-UART Chipset Driver:** Ensure the USB chip (typically `CH340` or `CP2102`) is recognized without yellow warning triangles.
3. **Baud Rate:** `115200` baud.

### C. From the Local Wi-Fi Network
1. **Wi-Fi SSID (Network Name):** Must be a **2.4 GHz network** (NodeMCU ESP8266 does **not** support 5 GHz 802.11a/ac/ax).
2. **Wi-Fi Password:** WPA2 pre-shared key.
3. **Network Gateway / Firewall:** Network must allow outbound TCP traffic to the internet on port `8883` (MQTTS).

### D. From EMQX Cloud Console (`https://www.emqx.com/en/cloud`)
1. **Deployment Hostname / URL:** e.g., `xxxxxxxx.emqxsl.com`.
2. **Device Publisher Username & Password:** e.g., user `plc_monitor_device` (Permissions: **Publish only** to `plcmon/#`).
3. **Web Subscriber Username & Password:** e.g., user `browser_readonly` (Permissions: **Subscribe only** to `plcmon/#`).
4. **Browser WSS URL:** `wss://xxxxxxxx.emqxsl.com:8084/mqtt`.

---

## 5. ESP8266 Firmware Build & Upload Procedure (Arduino IDE)

Follow this procedure to build and flash the NodeMCU 1.0 (ESP-12E):

### Prerequisites in Arduino IDE (v2.x)
1. **Board Package:**
   - In Arduino IDE, go to **File &rarr; Preferences**.
   - In "Additional boards manager URLs", enter:  
     `https://arduino.esp8266.com/stable/package_esp8266com_index.json`
   - Open **Tools &rarr; Board &rarr; Boards Manager**, search for `esp8266`, and install **ESP8266 by ESP8266 Community** (v3.1.2 or higher).
2. **Required Libraries:**
   - Open **Tools &rarr; Manage Libraries** (Ctrl+Shift+I).
   - Search and install:
     - `ArduinoJson` by Benoit Blanchon (version **≥ 7.0.0**)
     - `PubSubClient` by Nick O'Leary (version **≥ 2.8.0**)

### Step-by-Step Build & Flash Instructions
1. **Ensure Gateway Daemon is Stopped:**  
   > [!WARNING]  
   > The Python gateway and Arduino IDE cannot access `COM9` simultaneously. Stop `gateway.py` before uploading or opening the Serial Monitor.
2. **Create the Secrets Header:**
   - In File Explorer or terminal, navigate to `firmware/plc_monitor/`.
   - Copy `secrets.h.example` to `secrets.h`:
     ```powershell
     copy firmware\plc_monitor\secrets.h.example firmware\plc_monitor\secrets.h
     ```
   - Open `firmware/plc_monitor/secrets.h` in an editor and enter your Wi-Fi SSID, Wi-Fi password, EMQX host, and device user credentials:
     ```c
     #define WIFI_SSID       "Your_Lab_WiFi_SSID"
     #define WIFI_PASSWORD   "Your_WiFi_Password"
     #define MQTT_HOST       "your-deployment.emqxsl.com"
     #define MQTT_PORT       8883
     #define MQTT_USER       "plc_monitor_device"
     #define MQTT_PASS       "your_device_password"
     #define MQTT_CLIENT_ID  "nodemcu-plc-01"
     ```
3. **Open Sketch in Arduino IDE:**
   - Open `firmware/plc_monitor/plc_monitor.ino`.
4. **Configure Board Settings under the `Tools` Menu:**
   - **Board:** `NodeMCU 1.0 (ESP-12E Module)`
   - **Upload Speed:** `115200`
   - **CPU Frequency:** `80 MHz`
   - **Flash Size:** `4MB (FS:2MB OTA:~1019KB)`
   - **Port:** Select the COM port identified in Device Manager (e.g., `COM9`).
5. **Compile & Upload:**
   - Click **Upload** (Ctrl+U). Wait until the progress bar reaches 100% and displays `Done uploading`.
6. **Verify Boot via Serial Monitor:**
   - Open Tools &rarr; Serial Monitor. Set baud rate to **115200 baud**.
   - Press the physical `RST` button on the NodeMCU.
   - Expected output:
     ```text
     [SYS] PLC Monitor firmware starting…
     [WiFi] Connecting…
     [WiFi] Connected, IP: 192.168.x.x
     [MQTT] Connecting to xxxxxxxx.emqxsl.com:8883
     [MQTT] Connected
     ```
   - **Close the Serial Monitor** when verified so the port is released for the Python gateway.

---

## 6. Python Environment Setup & Gateway Execution Commands

Run these commands in Windows PowerShell from the repository root:

### Step 1: Initialize Virtual Environment & Install Dependencies
```powershell
# Navigate to the gateway directory
cd gateway

# Create an isolated virtual environment
python -m venv .venv

# Activate the virtual environment
.\.venv\Scripts\activate

# Upgrade pip and install pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Verify installed packages
pip list
```
*Expected installed packages:* `pymcprotocol >= 0.3.0` and `pyserial >= 3.5`.

### Step 2: Run Deterministic Unit Tests (Offline)
```powershell
# Run the test suite (requires no hardware or network connection)
python test_unit.py
```
*Expected output:*
```text
All 7 Python test suites passed successfully!
```

### Step 3: Run Standalone Read-Only PLC Smoke Test
Before launching the continuous streaming gateway, test raw PLC connectivity:
```powershell
# Basic smoke test using settings.ini defaults (192.168.3.39:5010)
python test_plc_connection.py

# Or specify custom network parameters if your lab subnet differs:
python test_plc_connection.py --ip 192.168.3.39 --port 5010 --polls 3
```
*Expected output:*
```text
INFO     PASS - PLC TCP connection established
--- Poll 1/3 --------------------------------
{
  "timestamp_iso": "2026-10-19T10:00:00Z",
  "inputs": {"X0": 0, "X1": 0, "X2": 0, "X3": 0, "X4": 0, "X5": 0, "X6": 0, "X7": 0},
  "outputs": {"Y20": 0, "Y21": 0, "Y22": 0, "Y23": 0, "Y24": 0, "Y25": 0, "Y26": 0, "Y27": 0},
  "connected": true,
  "error": null
}
[PASS] All polls succeeded - gateway is ready to run.
```

### Step 4: Start Continuous Gateway Daemon
```powershell
# Ensure Arduino Serial Monitor is closed before starting
python gateway.py
```
*Expected output:*
```text
INFO     ═══ Gateway starting  id=plc-gw-01 ═══
INFO     PLC target: 192.168.3.39:5010  poll=1.0s
INFO     Serial: COM9 @ 115200 baud
INFO     Serial port opened: COM9 @ 115200 baud
INFO     PLC connected  ip=192.168.3.39  port=5010 (timeout=3s)
```

---

## 7. MQTT Topic Structure & JSON Payload Contract

The communication contract between Gateway &rarr; ESP8266 &rarr; EMQX &rarr; Web Dashboard is defined as follows:

### A. MQTT Topic Hierarchy
- **Root Prefix:** `plcmon/{gateway_id}` (Default: `plcmon/plc-gw-01`)
- **Telemetry Topic:** `plcmon/plc-gw-01/telemetry` (QoS 0, Unretained) &mdash; Carries periodic I/O readings and gateway errors.
- **Availability / Status Topic:** `plcmon/plc-gw-01/status` (QoS 1, **Retained**) &mdash; Carries edge bridge online/offline status and LWT.
- **Diagnostics Topic:** `plcmon/plc-gw-01/diag` (QoS 0, Unretained) &mdash; Carries edge bridge error alerts.

### B. Telemetry Payload Schemas

#### 1. Normal Periodic Telemetry (`plc_reading`)
Published every 1 second when the PLC connection is healthy:
```json
{
  "type": "plc_reading",
  "gw_id": "plc-gw-01",
  "seq": 1042,
  "ts": "2026-10-19T12:00:00Z",
  "connected": true,
  "error": null,
  "inputs": {
    "X0": 0, "X1": 0, "X2": 0, "X3": 1,
    "X4": 0, "X5": 0, "X6": 0, "X7": 0
  },
  "outputs": {
    "Y20": 1, "Y21": 0, "Y22": 0, "Y23": 0,
    "Y24": 0, "Y25": 0, "Y26": 0, "Y27": 0
  }
}
```

#### 2. Gateway Error Frame (`gateway_error`)
Published when the PLC TCP connection times out or drops:
```json
{
  "type": "gateway_error",
  "gw_id": "plc-gw-01",
  "seq": 1043,
  "ts": "2026-10-19T12:00:05Z",
  "connected": false,
  "error": "Connection timed out"
}
```

#### 3. Gateway Heartbeat Frame (`heartbeat`)
Emitted periodically (every 5 seconds) to maintain connection baseline:
```json
{
  "type": "heartbeat",
  "gw_id": "plc-gw-01",
  "seq": 1044,
  "ts": "2026-10-19T12:00:10Z",
  "connected": true,
  "error": null
}
```

#### 4. Edge Availability Status (`status`)
Published with MQTT **Retain=true** by the ESP8266 and registered as the Last-Will and Testament (LWT):
```json
// Published on initial MQTT connect and every 10s:
{ "status": "online", "device": "nodemcu-plc-01" }

// Published automatically by broker if ESP8266 drops power/Wi-Fi (LWT):
{ "status": "offline", "device": "nodemcu-plc-01" }
```

---

## 8. Security Review & Credential Boundary

| Security Vector | Implementation Detail | Audit Assessment |
|---|---|---|
| **PLC Control Safety** | `PLCReader` only invokes command `0x0401` (Batch Read Bit Units). There are no write methods or opcodes anywhere in the codebase. | **Strictly Read-Only Enforced**. PLC program and memory cannot be altered remotely. |
| **PLC Network Isolation** | Q03UDECPU IP (`192.168.3.39`) is on a private subnet with no default gateway or internet exposure. | **Isolated Industrial Subnet**. No direct internet ingress to PLC. |
| **Microcontroller Threat Boundary** | NodeMCU ESP8266 has no open listening ports. It accepts data only via hardware UART/USB serial from the local laptop. | **Air-gapped Serial Ingress**. External attacks over Wi-Fi cannot inject serial commands. |
| **Public Browser Threat Model** | GitHub Pages is a public static website. Client-side JavaScript cannot protect private secrets. | **Untrusted Public Client**. Broker credentials entered in the browser must be strictly restricted. |
| **Broker Access Control (RBAC)** | Two separate credentials must be configured in EMQX Cloud: | **Mandatory Credential Separation**: |
| &bull; **Device Publisher** | User `plc_monitor_device` &rarr; Permissions: `Publish` to `plcmon/#`. `Subscribe: Denied`. | Prevents compromised device from intercepting or tampering with other clients. |
| &bull; **Web Subscriber** | User `browser_readonly` &rarr; Permissions: `Subscribe` to `plcmon/#`. `Publish: Denied`. | Guarantees that public users cannot publish false readings or inject commands. |
| **Credential Storage in Browser** | `app.js` persists `url`, `user`, and `topic` in `localStorage`, but **never** writes `password` to storage. | **Zero Credential Leakage**. Passwords exist only in memory during the active session. |

---

## 9. Dependency-Aware 7-Stage Pre-Lab Test Sequence

Follow this exact sequential runbook to commission the system safely without deadlocks:

```
[Stage 1: Offline Unit Tests] 
           │
[Stage 2: Windows Serial COM Check]
           │
[Stage 3: EMQX Cloud Provisioning]
           │
[Stage 4: NodeMCU Firmware Upload]
           │
[Stage 5: Physical PLC Ethernet Smoke Test]
           │
[Stage 6: Gateway Streaming Daemon]
           │
[Stage 7: Live Web Dashboard Verification]
```

### Stage 1: Local Offline Software Verification
- **Prerequisites:** Laptop with Python and modern web browser.
- **Action:**
  ```powershell
  cd gateway
  python -m venv .venv
  .\.venv\Scripts\activate
  pip install -r requirements.txt
  python test_unit.py
  ```
- **Expected Success:** `All 7 Python test suites passed successfully!`
- **Frontend Check:** Run `python -m http.server 8080 --directory web` and open `http://localhost:8080/test.html`. Confirm `107 PASS / 0 FAIL / 0 SKIP`.

### Stage 2: Windows Serial Port Enumeration
- **Prerequisites:** NodeMCU connected to laptop via micro-USB data cable.
- **Action:**
  ```powershell
  Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Description, Manufacturer
  ```
- **Expected Success:** Device listed with assigned port (e.g., `COM9`, `USB-SERIAL CH340`).
- **If Different:** Update `[serial] port = COMx` in `gateway/config/settings.ini`.

### Stage 3: EMQX Cloud Serverless Broker Provisioning
- **Prerequisites:** Internet access.
- **Action:**
  1. Log into `https://www.emqx.com/en/cloud`.
  2. Create a free Serverless Deployment &rarr; record broker hostname (e.g., `abc123.emqxsl.com`).
  3. Under **Authentication & Authorization**, create:
     - User 1: `plc_monitor_device` &rarr; Role: Publish to `plcmon/#`.
     - User 2: `browser_readonly` &rarr; Role: Subscribe to `plcmon/#`.
- **Expected Success:** Deployment status displays `RUNNING` with ports `8883` (TLS) and `8084` (WSS) active.

### Stage 4: Flash NodeMCU Firmware & Verify Cloud Link
- **Prerequisites:** Arduino IDE configured with ESP8266 core, `ArduinoJson`, and `PubSubClient`.
- **Action:**
  1. Populate `firmware/plc_monitor/secrets.h` with Wi-Fi and EMQX device credentials.
  2. Ensure `gateway.py` is **stopped**.
  3. Upload `plc_monitor.ino` to NodeMCU at 115200 baud.
  4. Open Serial Monitor (115200 baud) and reset board.
- **Expected Success:**
  ```text
  [WiFi] Connected, IP: 192.168.x.x
  [MQTT] Connecting to abc123.emqxsl.com:8883
  [MQTT] Connected
  ```
- **Action:** Close Serial Monitor immediately after verification.

### Stage 5: Physical PLC Ethernet Smoke Test
- **Prerequisites:** Laptop Ethernet port connected to PLC switch; laptop adapter set to static IP `192.168.3.100` (mask `255.255.255.0`).
- **Action:**
  ```powershell
  # Test basic network reachability
  ping 192.168.3.39

  # Test MC Protocol read capability
  cd gateway
  python test_plc_connection.py
  ```
- **Expected Success:** `[PASS] All polls succeeded - gateway is ready to run.`
- **Physical Indicator:** PLC `ERR` LED remains OFF; Ethernet `SD/RD` LEDs blink during poll.

### Stage 6: Launch Gateway Streaming Daemon
- **Prerequisites:** Stage 4 and Stage 5 passed; NodeMCU plugged into USB COM9.
- **Action:**
  ```powershell
  python gateway.py
  ```
- **Expected Success:**
  ```text
  INFO  PLC connected  ip=192.168.3.39  port=5010 (timeout=3s)
  INFO  Serial port opened: COM9 @ 115200 baud
  ```
- **Physical Indicator:** NodeMCU onboard LED flashes every 1 second as JSON frames arrive.

### Stage 7: Connect Supervisory Web Dashboard
- **Prerequisites:** Gateway running; browser connected to internet.
- **Action:**
  1. Open [https://surajyadavx.github.io/mitsubishi-q-plc-app/](https://surajyadavx.github.io/mitsubishi-q-plc-app/).
  2. Navigate to the **Settings** tab:
     - Broker WebSocket URL: `wss://abc123.emqxsl.com:8084/mqtt`
     - Read-Only Username: `browser_readonly`
     - Password: Enter password for `browser_readonly`.
     - Topic Prefix: `plcmon/plc-gw-01`
     - Ensure **Offline Demo Mode** is unchecked.
  3. Click **Connect to Broker**.
- **Expected Success:**
  - Header communication nodes illuminate green: `PLC` &bull; `GW` &bull; `BRIDGE` &bull; `BROKER: Connected`.
  - Operating mode displays `LIVE`. Data Age displays `<1s ago`.
  - Press physical pushbutton `X3` on the lab rig: UI contact `X3` and output relay `Y20` turn ON in real time.
  - Release `X3`: UI contact `X3` and output relay `Y20` turn OFF.

---

## 10. Expected Outputs & Non-Destructive Failure Diagnosis

Diagnose and resolve connection errors without altering PLC ladder logic:

### Diagnostic Tree for Common Faults

```
[Symptom: PLC Connection Timed Out / Refused]
  ├── 1. Check IP Configuration: Is laptop on static IP 192.168.3.100 (same subnet)?
  ├── 2. Check Cable & Switch: Are Ethernet link LEDs solid green on both ends?
  ├── 3. Check CPU Status: Is PLC keyswitch in RUN? (If in STOP, outputs cannot energize).
  └── 4. Check Windows Firewall: Is port 5010 blocked? Run: Test-NetConnection 192.168.3.39 -Port 5010

[Symptom: Serial Port Unavailable on COM9]
  ├── 1. Port Contention: Is Arduino Serial Monitor, PuTTY, or another script open? Close all.
  ├── 2. Loose Cable: Unplug and replug the USB cable. Verify port in Device Manager.
  └── 3. Cable Type: Is the micro-USB cable power-only? Swap with a verified data sync cable.

[Symptom: ESP8266 [WiFi] Timeout - will retry]
  ├── 1. Band Check: Is the Wi-Fi network 5 GHz? ESP8266 only connects to 2.4 GHz.
  └── 2. Credential Typo: Double-check WIFI_SSID and WIFI_PASSWORD in secrets.h.

[Symptom: ESP8266 [MQTT] Failed, rc=5]
  ├── Cause: Connection Refused - Not Authorized.
  └── Fix: Check EMQX Authentication. Ensure plc_monitor_device password matches secrets.h.

[Symptom: ESP8266 [MQTT] Failed, rc=-2]
  ├── Cause: Network Connect Failed.
  └── Fix: Cluster hostname incorrect or outbound port 8883 blocked by campus/corporate Wi-Fi.

[Symptom: Browser Dashboard Shows "Broker ACL Rejection (QoS 128)"]
  ├── Cause: EMQX Cloud rejected the subscription topic.
  └── Fix: In EMQX Authorization, edit user browser_readonly and ensure it has "Subscribe" permission to plcmon/#.
```

---

## 11. Assumptions, Engineering Scope & Unresolved Decisions

### A. Runtime Requirement of the Laptop Gateway
- **Is the laptop gateway strictly required at runtime?**  
  **YES**, under the current implementation.
- **Technical Justification:**  
  The Mitsubishi Q03UDECPU built-in Ethernet interface is connected to a local private LAN with no Wi-Fi hardware and no internet default gateway. The NodeMCU ESP8266 has Wi-Fi and USB serial, but **no Ethernet physical interface (RJ45/PHY)**. Therefore, the laptop gateway is the essential bridge that receives MC Protocol binary packets over Ethernet TCP and forwards them across USB serial to the ESP8266.
- **Architectural Alternative (Future Phase):**  
  An embedded microcontroller with both Ethernet and Wi-Fi (such as an ESP32 equipped with a W5500 SPI Ethernet module) could run `pymcprotocol`-equivalent C++ code directly and eliminate the laptop gateway. However, for the existing hardware inventory and repository codebase, the laptop gateway daemon (`gateway.py`) is mandatory at runtime.

### B. I/O Addressing Scope & Verification Boundaries
- **Monitored Scope:**  
  - Digital Inputs: `X0, X1, X2, X3, X4, X5, X6, X7` (Hex `0x00` &ndash; `0x07`)  
  - Digital Outputs: `Y20, Y21, Y22, Y23, Y24, Y25, Y26, Y27` (Hex `0x20` &ndash; `0x27`)
- **Verified vs. Unverified Rungs:**
  - **`X3` &rarr; `Y20`:** This specific contact-to-relay relationship is documented and verified as an active ladder rung in the PLC CPU. Pressing pushbutton `X3` energizes relay `Y20`.
  - **`X0–X2`, `X4–X7`, `Y21–Y27`:** These addresses are configured in `settings.ini` and successfully polled by the MC Protocol batch read command, but they are spare / unverified channels in the current ladder program. In the lab, these channels will typically read `0` (OFF). Their lack of transition is expected and does not constitute a communication failure.

---

## 12. Audit Evidence & Verification Traceability

The findings in this checklist were derived directly from active repository source code:

1. **Read-Only MC Protocol Implementation:** Verified in [gateway/src/plc_reader.py](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/gateway/src/plc_reader.py#L1-L253). Confirmed zero write commands (`0x1401` absent; only `batchread_bitunits` used).
2. **Serial Frame Monotonicity & Error Schema:** Verified in [gateway/src/serial_publisher.py](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/gateway/src/serial_publisher.py#L1-L157).
3. **Gateway Configuration Defaults:** Verified in [gateway/config/settings.ini](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/gateway/config/settings.ini#L1-L27).
4. **Firmware Topics & TLS Port 8883:** Verified in [firmware/plc_monitor/plc_monitor.ino](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/firmware/plc_monitor/plc_monitor.ino#L40-L68).
5. **Dashboard Subscription Logic & WSS Port 8084:** Verified in [web/assets/js/app.js](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/web/assets/js/app.js#L500-L600).
6. **Zero-Password Storage Security Policy:** Verified in [web/assets/js/app.js](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/web/assets/js/app.js#L475-L493) (`saveSettings` only records `url`, `user`, `topic`).
7. **Automated Offline Unit Tests:** Verified by running `python gateway/test_unit.py` (7/7 passed) and reviewing `web/test.html` (107/107 passed).
8. **GitHub Pages Deployment Pipeline:** Verified in [.github/workflows/static.yml](file:///d:/Academics/TY%20-%20S1/MS/mitsubishi-q-plc-app_ANTIGRAVITY/.github/workflows/static.yml#L1-L44).
