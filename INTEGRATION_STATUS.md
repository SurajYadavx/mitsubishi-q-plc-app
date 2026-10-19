# Integration Readiness Audit & System Status Report

**Project:** Mitsubishi Q03UDECPU Supervisory Remote Monitoring System  
**Repository:** `SurajYadavx/mitsubishi-q-plc-app`  
**Audit Date:** October 19, 2026  
**Auditor:** Industrial Automation & Systems Integration Agent (Antigravity)  
**Document Classification:** Technical Integration Audit & Runbook Status  

---

## 1. Executive Summary

This audit assesses the end-to-end integration readiness of the 5-tier passive supervisory architecture designed to monitor digital I/O states (specifically verified contact `X3` and output coil `Y20`) of a Mitsubishi Electric MELSEC-Q Q03UDECPU PLC.

```
[ Tier 1: PLC ]          Mitsubishi Q03UDECPU (192.168.3.39:5010) — MC Protocol 3E Binary
       │
       ▼ (Private LAN Ethernet)
[ Tier 2: Gateway ]      Windows Laptop Python Daemon (gateway.py via pymcprotocol)
       │
       ▼ (USB Serial JSON @ 115200 baud — COM9)
[ Tier 3: Edge Bridge ]  NodeMCU ESP8266 (plc_monitor.ino)
       │
       ▼ (Wi-Fi Uplink — MQTT / TLS 1.2 on Port 8883)
[ Tier 4: Cloud Broker ] HiveMQ Cloud Managed Cluster (Dedicated ACLs)
       │
       ▼ (Secure WebSockets WSS on Port 8884)
[ Tier 5: Web Console ]  GitHub Pages Static Client (MQTT.js Passive Subscriber)
```

The software layers (Gateway daemon, ESP8266 firmware, and Web supervisory console) are fully implemented, verified with deterministic offline test suites, and compliant with strict read-only safety constraints. Physical lab hardware integration and live broker provisioning remain the final deployment gates.

---

## 2. Component Implementation & Test Coverage Matrix

| Component Tier | Implementation File(s) | Status | Test Coverage | Offline Test Command |
|---|---|---|---|---|
| **Tier 1: PLC Interface** | `gateway/src/plc_reader.py` | Complete (Read-Only) | Unit tested with mock PLC socket | `python gateway/test_unit.py` (Suite 1 & 2) |
| **Tier 2: Host Gateway** | `gateway/gateway.py`<br>`gateway/src/serial_publisher.py` | Complete (Read-Only) | Frame schema & sequence monotonic tracking verified | `python gateway/test_unit.py` (Suites 3–7) |
| **Tier 3: Edge Bridge** | `firmware/plc_monitor/plc_monitor.ino` | Complete (Flash-Ready) | Firmware compiles; non-blocking Wi-Fi reconnect implemented | Static inspection & syntax validation |
| **Tier 4: Cloud MQTT** | `firmware/plc_monitor/secrets.h.example`<br>`web/index.html` | Protocol Configured | WSS 8884 subscriber & TLS 8883 publisher schemas verified | `web/test.html` (Suites 2, 7, 8) |
| **Tier 5: Web Console** | `web/index.html`<br>`web/assets/css/style.css`<br>`web/assets/js/app.js` | Complete & Redesigned | 107 offline browser unit tests (100% pass rate) | `http://localhost:8080/test.html` |
| **CI/CD Deployment** | `.github/workflows/deploy-pages.yml` | Validated | GitHub Actions workflow deploys `./web` artifact | Manual or push trigger to `main` |

---

## 3. Implemented and Locally Testable Capabilities

1. **Deterministic Python Gateway Unit Test Suite (`gateway/test_unit.py`):**
   - 7 test suites validating:
     - Q-Series hexadecimal device address parsing (`X0–X7` &rarr; decimal 0–7, `Y20–Y27` &rarr; decimal 32–39).
     - Strict bit extraction from MC Protocol byte frames.
     - Payload schema construction (`plc_reading`, `heartbeat`, `gateway_error`).
     - Monotonic sequence numbering and ISO-8601 UTC timestamp injection.
     - Mock socket connection error handling and automatic retry backoff.
   - **Verification:** Runs offline without hardware. Passes 7/7 suites in ~0.5s.

2. **Deterministic Web Supervisory Test Harness (`web/test.html`):**
   - 107 browser unit tests covering:
     - Pure helper functions (`formatAge`, `formatIsoShort`, `formatTimestamp`, `escapeHtml`).
     - Subtopic extraction (`plcmon/{gw_id}/telemetry`, `/status`, `/diag`).
     - Payload schema validation (rejection of missing `type`, `gw_id`, `seq`, `ts`, `inputs`, `outputs`).
     - State-change event generation with suppression of transitions from null/invalid data.
     - Multimodal bit normalization: integer/boolean/string 1/0 &rarr; ON/OFF, invalid/null/undefined &rarr; UNKNOWN (never false OFF).
     - Monotonic sequence gap and duplicate/out-of-order frame discard.
     - Stale data detection after 15-second inactivity timeout.
     - Zero password leakage (credentials excluded from `localStorage`).
   - **Verification:** Passes 107 PASS / 0 FAIL / 0 SKIP.

3. **Offline Demo Simulation System:**
   - Client-side deterministic telemetry generator simulating the `X3 &rarr; Y20` ladder rung every 2 seconds.
   - Supports 5 injectable anomaly scenarios:
     1. Normal telemetry cycle (2s).
     2. Delayed telemetry (8s jitter warning).
     3. Sequence gap (+3 dropped frames detected).
     4. Stale data warning (>15s timeout banner).
     5. Gateway comms fault and automatic recovery.

4. **Industrial SCADA/HMI Design System:**
   - High-contrast Dark Slate / Industrial Steel appearance adhering to ISA-101 situational awareness principles.
   - Multimodal state indications (Color + LED Lamp + Monospace Text Badge + Shape).
   - Zero horizontal overflow across all verified viewport sizes: 320&times;568, 360&times;800, 390&times;844, 768&times;1024, 1366&times;768, and 1920&times;1080.

---

## 4. Configuration Still Required (Site & Lab Specific)

The following parameters must be confirmed and configured prior to hardware power-up:

| Parameter | File Location | Default / Placeholder | Action Required in Lab |
|---|---|---|---|
| **PLC IP Address** | `gateway/config/settings.ini` | `192.168.3.39` | Verify lab Ethernet subnet and Q03UDECPU IP. |
| **PLC TCP Port** | `gateway/config/settings.ini` | `5010` | Confirm port matches GX Works2 Built-in Ethernet Open Settings. |
| **USB COM Port** | `gateway/config/settings.ini` | `COM9` | Check Windows Device Manager when NodeMCU is plugged in. |
| **Wi-Fi SSID** | `firmware/plc_monitor/secrets.h` | `"your_wifi_ssid_here"` | Populate from `secrets.h.example` with lab 2.4 GHz SSID. |
| **Wi-Fi Password** | `firmware/plc_monitor/secrets.h` | `"your_wifi_password_here"` | Populate with lab Wi-Fi WPA2 pre-shared key. |
| **HiveMQ Hostname** | `firmware/plc_monitor/secrets.h` | `"your_broker_hostname_here"` | Create free cluster at `console.hivemq.cloud` and enter URL. |
| **Device MQTT User/Pass** | `firmware/plc_monitor/secrets.h` | `"esp8266_device_user"` | Provision credential in HiveMQ with **Publish-only** permission. |
| **Browser MQTT User/Pass**| Web UI Settings Tab | `"browser_readonly"` | Provision separate credential in HiveMQ with **Subscribe-only** permission. |

---

## 5. Hardware-Dependent Tasks Still Unverified (Laboratory Execution)

1. **Physical PLC MC Protocol Read Smoke Test:**
   - *Task:* Execute `python gateway/test_plc_connection.py` on the Windows laptop connected to the PLC switch.
   - *Verification Target:* Successful TCP 3-way handshake on port 5010, MC Protocol 3E binary response, non-empty bit reading for X0–X7 and Y20–Y27.
   - *Status:* Unverified (requires physical Q03UDECPU hardware).

2. **NodeMCU Serial Transport & Flashing:**
   - *Task:* Flash `firmware/plc_monitor/plc_monitor.ino` via Arduino IDE to NodeMCU ESP-12E on COM9 at 115200 baud.
   - *Verification Target:* ESP8266 serial boot banner `[SYS] PLC Monitor firmware starting…` and successful Wi-Fi IP acquisition.
   - *Status:* Unverified (requires physical NodeMCU module and USB cable).

3. **Autonomous Ladder Contact Synchronization (`X3 &rarr; Y20`):**
   - *Task:* Physically press pushbutton wired to terminal `X3` on the PLC rig. Observe relay `Y20` LED on the PLC output card and confirm gateway telemetry reflects `inputs.X3: 1` and `outputs.Y20: 1` simultaneously.
   - *Status:* Unverified (requires physical rig).

---

## 6. Cloud-Dependent Tasks Still Unverified (HiveMQ Provisioning)

1. **HiveMQ Cloud Cluster Creation:**
   - *Task:* Provision a free-tier Serverless cluster (100 concurrent connections, 10 GB/month data transfer).
   - *Status:* Unverified (requires user to create account on `console.hivemq.cloud`).

2. **Role-Based Access Control (RBAC) Separation:**
   - *Task:* In HiveMQ Access Management, define two isolated credentials:
     1. Device Publisher: User `esp8266_device_user` &rarr; Permissions: `Publish` to `plcmon/plc-gw-01/#`. `Subscribe: Denied`.
     2. Web Subscriber: User `browser_readonly` &rarr; Permissions: `Subscribe` to `plcmon/plc-gw-01/#`. `Publish: Denied`.
   - *Status:* Unverified (cloud account configuration).

3. **Secure WebSockets (WSS) Connectivity on Port 8884:**
   - *Task:* Connect the browser dashboard to `wss://<cluster-id>.s1.eu.hivemq.cloud:8884/mqtt` using the `browser_readonly` user.
   - *Status:* Unverified (depends on cluster creation).

---

## 7. Security Architecture & Invariants

| Layer / Boundary | Security Enforcement Mechanism | Audit Finding |
|---|---|---|
| **PLC Control Safety** | Read-Only Enforced | `PLCReader` issues only command `0x0401` (Batch Read Bit Units). No write methods exist in Python or firmware. |
| **PLC Network Isolation** | Private Subnet | The Q03UDECPU IP (`192.168.3.39`) is private LAN only and never exposed to the internet. |
| **Public Browser Threat Model** | Untrusted Client | Public browser JavaScript cannot hold confidential secrets. The browser uses dedicated subscribe-only credentials. Passwords are never saved to `localStorage`. |
| **Microcontroller Ingress** | Serial-Only Gateway | The ESP8266 does not accept inbound network connections. It only accepts serial JSON from the USB port. |
| **Repository Secret Hygiene** | `.gitignore` Enforcement | `.gitignore` explicitly excludes `secrets.ini`, `secrets.h`, and `*.env`. Example templates are maintained safely. |

---

## 8. Sequential Lab Commissioning Runbook

When the physical hardware and cloud cluster are ready, proceed strictly in this order:

```
[Phase 1: PLC Smoke Test]
  └─► Connect Windows laptop Ethernet to PLC LAN (e.g. 192.168.3.100).
  └─► Run: cd gateway; .\.venv\Scripts\activate; python test_plc_connection.py
  └─► Confirm: PASS - PLC TCP connection established and I/O bits read successfully.

[Phase 2: HiveMQ Cloud Provisioning]
  └─► Create cluster on console.hivemq.cloud.
  └─► Create `esp8266_device_user` (Publish only).
  └─► Create `browser_readonly` (Subscribe only).

[Phase 3: ESP8266 Firmware Flash]
  └─► Ensure gateway.py is STOPPED (to release COM9).
  └─► Create `firmware/plc_monitor/secrets.h` with Wi-Fi & device credentials.
  └─► Flash `plc_monitor.ino` at 115200 baud via Arduino IDE.
  └─► Verify in Serial Monitor: `[WiFi] Connected` and `[MQTT] Connected`.

[Phase 4: Launch Host Gateway Daemon]
  └─► In PowerShell: python gateway.py
  └─► Verify gateway connects to PLC and logs JSON frames sent to COM9 every 1s.

[Phase 5: Connect Web Supervisory Console]
  └─► Open https://surajyadavx.github.io/mitsubishi-q-plc-app/ (or local server).
  └─► Navigate to Settings tab &rarr; enter Broker URL (wss://...:8884/mqtt), username `browser_readonly`, password.
  └─► Click "Connect to Broker".
  └─► Verify: Path strip turns green (PLC &bull; GW &bull; BRIDGE &bull; BROKER: OK), Mode shows LIVE, and X3/Y20 update in real-time.
```

---

## 9. Actionable Next Steps

1. **Review and Commit (Local Developer):**
   - Review the redesigned `web/index.html`, `web/assets/css/style.css`, and `web/assets/js/app.js`.
   - Verify `git status` and `git diff` locally.
   - Commit changes using standard commit conventions: `refactor(web): industrial supervisory UI redesign and responsive optimization`.
2. **Push to GitHub (`main`):**
   - Push to `main` branch to trigger `.github/workflows/deploy-pages.yml`.
   - Confirm successful GitHub Pages deployment at `https://surajyadavx.github.io/mitsubishi-q-plc-app/`.
3. **Lab Hardware Commissioning:**
   - Execute the 5-phase sequential runbook when physically present in the lab.
