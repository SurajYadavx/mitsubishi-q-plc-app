# Mitsubishi Q-PLC Remote Monitor

A production-oriented, **read-only** remote monitoring system for the Mitsubishi Q03UDECPU PLC.

> **First release is strictly read-only.**  
> This system cannot write to the PLC, force outputs, or modify ladder logic.

## Architecture

```
PLC (192.168.3.39:5010, MC Protocol)
  → Windows Gateway (Python, pymcprotocol)
    → USB Serial JSON (COM9, 115200 baud)
      → NodeMCU ESP8266 (Arduino firmware)
        → Wi-Fi → MQTT/TLS (EMQX Cloud)
          → GitHub Pages Dashboard (MQTT.js / WSS)
```

See [docs/architecture.md](docs/architecture.md) for full detail.

## Project Structure

```
mitsubishi-q-plc-app/
├── web/                          # Static GitHub Pages frontend
│   ├── index.html                # Dashboard
│   └── assets/
│       ├── css/style.css         # Industrial dark-mode stylesheet
│       └── js/app.js             # MQTT client, telemetry logic, UI
│
├── gateway/                      # Windows Python PLC gateway
│   ├── gateway.py                # Main entry point
│   ├── requirements.txt          # pymcprotocol, pyserial
│   ├── test_plc_connection.py    # Read-only smoke-test
│   ├── config/
│   │   ├── settings.ini          # PLC IP, serial port, poll interval
│   │   └── secrets.ini.example   # Template — copy, fill, do NOT commit
│   ├── src/
│   │   ├── plc_reader.py         # MC Protocol reader (read-only)
│   │   └── serial_publisher.py   # JSON serial writer
│   └── logs/                     # Runtime logs (git-ignored)
│
├── firmware/                     # NodeMCU ESP8266 Arduino firmware
│   └── plc_monitor/
│       ├── plc_monitor.ino       # Main sketch
│       └── secrets.h.example     # Template — copy, fill, do NOT commit
│
├── docs/                         # Documentation
│   ├── architecture.md           # System design, topic schema, security
│   ├── gateway-setup.md          # Python gateway setup guide
│   ├── firmware-setup.md         # Arduino / ESP8266 setup guide
│   └── deployment.md             # GitHub Pages deployment
│
├── LAB_HANDOVER.md               # Lab commissioning runbook & integration guide
├── .gitignore                    # Excludes secrets, venvs, logs
└── README.md                     # This file
```

## Lab Commissioning & Runbook

For physical lab integration and on-site hardware commissioning, refer directly to the **[Lab Handover Procedure & Integration Runbook](LAB_HANDOVER.md)** for:
- Required hardware/software checklists
- Lab configuration checklist (PLC IP, COM port, EMQX Cloud broker)
- Exact sequential execution steps (PLC smoke-test → serial verification → firmware flashing → gateway startup → MQTT validation → browser verification)
- Diagnostic success indicators and recovery playbooks for common connection failures
- Boundary between verified offline behavior and unverified live behavior

## Quick Start

### 1. Gateway (Windows Laptop)

```powershell
cd gateway
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Smoke-test PLC connection (read-only)
python test_plc_connection.py

# Start the gateway
python gateway.py
```

See [docs/gateway-setup.md](docs/gateway-setup.md) for full instructions.

### 2. ESP8266 Firmware

1. Copy `firmware/plc_monitor/secrets.h.example` → `secrets.h`
2. Fill in Wi-Fi and MQTT credentials
3. Open `plc_monitor.ino` in Arduino IDE and flash to NodeMCU

See [docs/firmware-setup.md](docs/firmware-setup.md) for full instructions.

### 3. MQTT Broker (EMQX Cloud Serverless)

1. Create a free Serverless deployment at https://www.emqx.com/en/cloud
2. Note the deployment broker hostname (e.g., `xxxxxx.emqxsl.com`)
3. Create two client credential sets in **Authentication**:
   - `plc_monitor_device` — publish to `plcmon/#`
   - `browser_readonly` — subscribe to `plcmon/#` only
4. Use WebSocket port **8084** (WSS) with path `/mqtt` for the browser
5. Use native MQTT TLS port **8883** for the ESP8266

### 4. Frontend & Offline Execution

No physical hardware, live PLC, or MQTT broker is needed to run and inspect the full application:

```powershell
# Serve the static web application locally
python -m http.server 8080 --directory web

# Open in browser:
# Main Application: http://localhost:8080/index.html
# Automated Tests:  http://localhost:8080/test.html
```

To run Python unit tests:
```powershell
python gateway/test_unit.py
```

### 5. Offline Demo Mode & Communication Fault Simulation

1. Navigate to the **Settings** tab and check **Toggle Offline Demonstration Mode**.
2. The system generates deterministic telemetry pulses every 2 seconds, cycling the `X3` input contact and driving `Y20` in synchronization.
3. Test communication fault scenarios via the scenario control pills:
   - **Normal (2s Cycle):** Nominal scan cadence (X3 pulse driving Y20).
   - **Delayed Telemetry (8s):** Demonstrates latency degradation and stale warnings.
   - **Sequence Gap (+3):** Injects skipped packet sequence numbers to verify gap detection.
   - **Stale Data (>15s):** Halts telemetry delivery to verify the `TELEMETRY STALE` banner and tile degradation.
   - **Gateway Fault & Recovery:** Simulates host gateway COM9 timeout with automatic recovery after 6 seconds.
4. **Pause/Resume** and **Reset** controls allow repeatable demonstrations.

### 6. Windows Local Agent REST API (Phase 04A)

The platform provides a strictly read-only local FastAPI service exposing telemetry snapshots, device inventory, and engine diagnostics to local engineering tools, browser frontends, and SCADA overlays over loopback (`127.0.0.1:8000`).

```powershell
# Run the Local Agent in offline simulation mode:
.\gateway\.venv\Scripts\python.exe -m agent.cli --simulate

# Run with custom port and API key authentication:
.\gateway\.venv\Scripts\python.exe -m agent.cli --simulate --port 8000 --api-key your-secret-key

# View interactive OpenAPI / Swagger documentation:
# Open in browser: http://127.0.0.1:8000/docs
```

To run the full unit test suite (77 tests covering Core, Modbus, and Local Agent):
```powershell
.\gateway\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

#### Read-Only REST Endpoints (`/api/v1`)
| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/health` | `GET` | Gateway health, uptime, polling metrics, error count |
| `/api/v1/devices` | `GET` | List of all configured PLC devices and connection status |
| `/api/v1/devices/{device_id}` | `GET` | Detailed metadata and point definitions for a device |
| `/api/v1/devices/{device_id}/telemetry` | `GET` | Latest telemetry snapshot with point values and quality codes |
| `/api/v1/devices/{device_id}/diagnostics` | `GET` | Adapter-level diagnostics, round-trip times, and error stats |
| `/api/v1/agent/info` | `GET` | Host OS, Python version, agent version, security profile |
| `/api/v1/config/status` | `GET` | Active configuration summary with secrets strictly redacted |

> **Security Note:** The Local Agent binds exclusively to `127.0.0.1` by default and rejects non-loopback bindings (`0.0.0.0`) unless `--allow-remote` is explicitly specified. It contains zero write, force, or control endpoints.

## Supervisory Engineering Features (Stages 1.6 – 1.8)

### 1. Dual Visual Themes (Dark & Light)
- **Dark Slate Theme (Default):** Tailored for 24/7 low-glare industrial control room environments.
- **Light Industrial Theme:** High-contrast steel/slate theme designed for bright ambient shop floor and field tablet inspections.
- **Status Accessibility:** Preserves strict distinctions between `ON`, `OFF`, `UNKNOWN`, `STALE`, and `FAULT` with text badges and borders—never relying on color alone.
- **Theme Persistence:** Stores visual preference in `localStorage` under `qplc_theme` without ever saving credentials or tokens. Early anti-flash bootstrap prevents layout flickers on page load.

### 2. Industrial Monitoring Semantics & Data Integrity (Stage 1.8 Quality Gate)
- **Strict Bit Normalization:** Supported ON values (`1`, `true`, `"1"`, `"true"`, `"on"`) and OFF values (`0`, `false`, `"0"`, `"false"`, `"off"`). All missing, malformed, null, or out-of-spec values resolve to `UNKNOWN` (`null`) and are never displayed as false OFF.
- **State Change Suppression:** Invalid or corrupted data never triggers false ON/OFF transition events.
- **Duplicate Frame Protection:** Duplicate packets sharing identical sequence numbers are discarded to avoid corrupting telemetry statistics.
- **Out-of-Order Frame Rejection:** Delayed packets with older sequence numbers are discarded to prevent live device states from regressing backwards in time.
- **Gateway Reboot Handling:** Clean sequence counter resets (e.g. sequence resetting to 1) re-synchronize the baseline cleanly.
- **Retained / Historic Telemetry Detection:** Packets generated >15 seconds ago (such as MQTT retained messages from previous sessions) are immediately marked as `STALE` rather than presented as live.
- **Missing Dependency Resilience:** If the MQTT client library fails to load from the CDN, the application displays an informative notification without throwing unhandled exceptions.

### 3. Static Ladder Diagram Viewer
- Illustrates verified ladder logic extracted from the GX Works2 project: `LD X003` &rarr; `OUT Y020`.
- Displayed on both the **Overview Dashboard** and the **PLC I/O Monitor Tab**.
- Displays dynamic conductive path continuity, power rails (+24VDC / 0VDC), contact symbols (`⊣ ⊢`, `⊣■⊢`, `⊣ ? ⊢`), and coil states.
- Clearly labeled as an illustrative representation; does not control or force PLC coils.

### 4. Information Architecture & Presentation
- **Overview:** Subsystem telemetry health matrix with independent states (Broker, Bridge, Gateway, PLC), freshness age metrics, verified ladder status, and recent events.
- **PLC I/O Monitor:** Dual-view presentation offering a **Card Grid** (visual LED indicators) and a **Compact Engineering Diagnostics Table** (hex addresses, point types, signal bars, and timestamps).
- **Event Journal:** Real-time event log with multi-category filters (`I/O Changes`, `Comms Faults`, `System`), severity levels (`INFO`, `WARN`, `ERROR`), real-time search, and shift handover CSV export.
- **System Diagnostics:** Telemetry statistics counters (frame counts, heartbeats, gaps, discards, transitions) and 5-tier signal path breakdown.
- **Settings:** Browser-local configuration, visual theme picker, and demo mode manager. Passwords are never saved in storage.

## Hardware Required (For Future Stage 2)

| Item | Detail |
|---|---|
| Mitsubishi Q03UDECPU | With Ethernet module, MC Protocol enabled on port 5010 |
| Windows laptop | On the same LAN as the PLC, Python 3.11 installed |
| NodeMCU ESP8266 | USB cable (data), powered from laptop USB |
| Wi-Fi router | ESP8266 must reach the internet |
| EMQX Cloud account | Serverless free tier is sufficient |
| GitHub account | For Pages hosting |

## Verification Milestones

| Stage | Description | Status |
|---|---|---|
| 1.0–1.5 | Architecture, project structure, read-only gateway, NodeMCU firmware | ✅ Complete |
| 1.6 | Supervisory UI/UX overhaul, 5 tabs, dual I/O views, CSV export, hex addressing | ✅ Verified (80 tests) |
| 1.7 | Dark/Light themes, demo fault simulation, ladder viewer, 100 tests, offline readiness | ✅ Verified (100 tests, 6 Python suites) |
| 1.8 | Final Quality Gate & Feature Freeze: Retained message protection, duplicate/out-of-order rejection, dependency safety, public-client audit | ✅ Complete & Feature Frozen (107 frontend unit tests, 6 Python suites) |
| **1.9** | **Offline Deployment Readiness & Lab Preparation: Full config audit, PyPI pymcprotocol fix, Q-series hex addressing, ASCII logs, LAB_HANDOVER.md runbook** | **✅ Complete (107 frontend unit tests, 7 Python suites)** |
| 2.0 | Physical PLC Ethernet connection & live MC Protocol 3E smoke-test | ⏳ Hardware Required |
| 3.0 | NodeMCU ESP8266 flashing & serial COM9 forwarding | ⏳ Hardware Required |
| 4.0 | EMQX Cloud Serverless broker provisioning & WSS credentials | ⏳ Broker Config Required |
| 5.0 | End-to-end live telemetry verification & GitHub Pages deployment | ⏳ Hardware + Cloud Required |

## Security & Public Release Policy

- **Strictly Read-Only:** The application contains zero physical output write functionality, force operations, PLC write opcodes (`0x1401`), or ladder modification capabilities.
- **Network Isolation:** The PLC's private IP address (`192.168.3.39`) and TCP port (`5010`) remain within the local LAN and are **never** exposed to the internet.
- **Public-Client Reality:** In browser-based web applications hosted on public static hosts (e.g., GitHub Pages), client-side JavaScript credentials are fully visible to device users and network inspectors. Credentials configured in the web interface must belong to a dedicated subscriber account configured with strictly limited, read-only permissions on `plcmon/plc-gw-01/#` with zero publish or broker administrative access.
- **Credential Hygiene:** `secrets.ini` and `secrets.h` are listed in `.gitignore` and must never be committed. Passwords are never saved in `localStorage`.
- **Zero Live Secrets Committed:** All configuration files and examples use templated placeholders.

## Existing PLC Logic

The Q03UDECPU contains a rung `X3 → Y20` (contact X3 controls coil Y20).  
This system does not modify, override, or interact with that logic.  
Y21–Y27 usage must be verified in GX Works2 before any future feature considers them.

## License

MIT — See [LICENSE](LICENSE) file.
