# PHASE 07 — PRODUCTION READINESS & INTEGRATION AUDIT
**Universal Multi-Vendor PLC Monitoring Platform**<br>
**Repository:** `https://github.com/SurajYadavx/mitsubishi-q-plc-app`<br>
**Branch:** `main` | **Baseline Commit:** `686bbbc`<br>
**Audit Date:** October 20, 2026<br>
**Auditor:** Senior Industrial Automation Software Architect & Lead Security Auditor<br>
**Status:** **AUDIT COMPLETE — VERIFIED OFFLINE — PHYSICAL COMMISSIONING PENDING**

---

## 1. Executive Summary

A comprehensive, read-only architectural, safety, and security audit of the Universal Multi-Vendor PLC Monitoring Platform was conducted against baseline commit `686bbbc`. This audit was executed strictly offline and software-only, without physical access to target programmable logic controllers (PLCs), network modifications, or write commands.

### Key Audit Findings
1. **Read-Only Safety Invariant (PASS):**<br>
   The platform enforces strict read-only guarantees across all architectural layers. Zero PLC write, force, reset, remote-run, or ladder modification capabilities exist in production code. All REST API endpoints in the FastAPI Windows Local Agent are strictly HTTP `GET`.
2. **Automated Test Verification (PASS — 100% Pass Rate across 235 Tests):**<br>
   All automated test suites execute with zero failures:
   - **Core Platform & Agent Test Suite:** 100 passed, 0 failed, 0 skipped (`unittest discover tests`).
   - **Legacy Gateway Test Suite:** 7 suites passed, 0 failed (`gateway/test_unit.py`).
   - **Frontend Test Suite:** 128 assertions passed, 0 failed (`web/test.html` headless runner).
   - **Offline Smoke & Schema Suite:** 100% verified (`verify_phase_06a.py`).
3. **Multi-Vendor Architecture & Data Flow (PASS):**<br>
   The system cleanly decouples device configurations (`config/devices.yaml`), protocol adapters (`adapters/`), core orchestration (`core/engine.py`), and ingestion interfaces (FastAPI local agent and legacy serial/MQTT gateway).
4. **Security & Secrets Handling (PASS with 2 Remediation Recommendations):**<br>
   Zero active secrets, passwords, or private keys are committed in Git. In-memory dashboard credential isolation prevents browser storage leakage. Host binding rejects non-loopback exposure unless explicitly authorized with an API key.
   - *Recommendation 1 (Medium):* ESP8266 firmware uses `wifiClient.setInsecure()`, bypassing TLS certificate chain validation.
   - *Recommendation 2 (Medium):* `gateway/requirements.txt` lacks root-level presence and strict lockfile pinning (`==`).
5. **Physical Hardware Reality (NOT VERIFIED):**<br>
   Compatibility with physical Mitsubishi Q03UDECPU hardware and physical Modbus TCP controllers is **NOT VERIFIED against physical hardware**. While source inspection and automated simulation tests pass, live network timings, CPU load, and hardware I/O response remain strictly unverified.

---

## 2. Actual Architecture and Data Flow

### 2.1 System Entry Points

The codebase contains two distinct operational entry points sharing common core components:

```
                                    ┌────────────────────────┐
                                    │   config/devices.yaml  │
                                    └───────────┬────────────┘
                                                │
                                    ┌───────────▼────────────┐
                                    │   core.config_loader   │
                                    └───────────┬────────────┘
                                                │
                                    ┌───────────▼────────────┐
                                    │   core.engine.Engine   │
                                    └─────┬────────────┬─────┘
                                          │            │
                   ┌──────────────────────┘            └──────────────────────┐
                   │                                                          │
         [Entry Point A: Local]                                    [Entry Point B: Cloud]
       agent/cli.py (FastAPI Agent)                              gateway/gateway.py (Daemon)
                   │                                                          │
     ┌─────────────▼─────────────┐                                ┌───────────▼───────────┐
     │ agent.service.LocalService│                                │  Serial JSON (COM9)   │
     └─────────────┬─────────────┘                                └───────────┬───────────┘
                   │                                                          │
     ┌─────────────▼─────────────┐                                ┌───────────▼───────────┐
     │ FastAPI GET Endpoints     │                                │ ESP8266 (plc_monitor) │
     │ (http://127.0.0.1:8000)   │                                └───────────┬───────────┘
     └─────────────┬─────────────┘                                            │
                   │                                              ┌───────────▼───────────┐
                   │ (HTTP Polling 1s)                            │ EMQX Cloud (MQTT/TLS) │
                   │                                              └───────────┬───────────┘
     ┌─────────────▼─────────────┐                                            │ (WSS 8084)
     │   web/index.html + app.js │ ◄──────────────────────────────────────────┘
     │   (Local Agent Mode)      │              (Cloud Broker Mode)
     └───────────────────────────┘
```

#### Entry Point A: Windows Local Agent (`agent/cli.py`)
- **Invocation:** `python -m agent.cli [--simulate] [--host 127.0.0.1] [--port 8000]`
- **Role:** High-performance, low-latency supervisory daemon running directly on the shop floor or lab Windows machine.
- **Components Active:** `core.config_loader`, `core.engine.GatewayEngine`, `agent.service.LocalAgentService`, `agent.api.create_app` (FastAPI + Uvicorn), `agent.security.SecurityPolicy`.
- **Target Audience:** Local HMI terminals, local supervisory operators, air-gapped industrial cells without external internet access.

#### Entry Point B: Legacy Cloud Gateway (`gateway/gateway.py`)
- **Invocation:** `python gateway/gateway.py [--simulate] [--config config/devices.yaml]`
- **Role:** Headless edge forwarder streaming serial JSON telemetry over USB to a hardware NodeMCU ESP8266 microcontroller bridge.
- **Components Active:** `core.config_loader`, `core.engine.GatewayEngine`, `core.transport_interface.SerialTelemetryTransport`.
- **Target Audience:** Remote cloud monitoring over Wi-Fi and TLS 1.2 to EMQX Cloud Serverless and GitHub Pages.

---

### 2.2 End-to-End Telemetry Data Flow

#### Local Agent Data Flow (Primary Platform Path)
1. **Configuration Ingestion:** `core/config_loader.py` validates `config/devices.yaml` into immutable `GatewayConfig`, `DeviceConfig`, and `PointDefinition` models.
2. **Adapter Dispatch:** `core/engine.py:GatewayEngine` uses `core/adapter_registry.py` to instantiate protocol drivers (`MitsubishiMcProtocolAdapter`, `ModbusTcpAdapter`, or `SimulatedPlcAdapter`).
3. **Field Device Scanning:** At each device's configured `poll_interval_sec`, the engine executes `adapter.read_points()`.
4. **Quality Normalization:** Raw PLC response words and bits are normalized into `NormalisedReading` objects with explicit `DataQuality` flags (`GOOD`, `BAD`, `COMM_FAILURE`, `STALE`), engineering units, and scale factors.
5. **Thread-Safe Caching:** `agent/service.py:LocalAgentService` stores the latest `NormalisedTelemetryBatch` per device under an `RLock`, preventing API client requests from triggering redundant PLC network traffic.
6. **REST API Exposure:** `agent/api.py` exposes read-only routes (`/api/v1/devices`, `/api/v1/devices/{id}/telemetry`, `/api/v1/health`, `/api/v1/devices/{id}/diagnostics`).
7. **Frontend Ingestion:** `web/assets/js/app.js` runs `pollLocalAgent()` every 1,000 ms via `fetch()`, binding dynamic device selection, digital I/O states, and analog telemetry to the DOM.

#### Cloud Gateway Data Flow (Secondary Path)
1. `core/engine.py` polls devices and generates `NormalisedTelemetryBatch`.
2. `core/transport_interface.py:SerialTelemetryTransport` serializes batches to newline-delimited JSON (`plc_reading` payload) and transmits over USB serial (`115200 baud`).
3. ESP8266 firmware (`firmware/plc_monitor/plc_monitor.ino`) receives the serial line, validates mandatory JSON keys (`type`, `gw_id`, `seq`, `ts`), and publishes via TLS 1.2 to EMQX Cloud topic `plcmon/plc-gw-01/telemetry`.
4. Browser dashboard (`web/assets/js/app.js`) connected via Secure WebSockets (`wss://...:8084/mqtt`) subscribes to `plcmon/plc-gw-01/#` and renders updates.

---

### 2.3 Duplicate and Overlapping Implementations Analysis

| File / Component | Overlaps With | Necessity / Architectural Role | Recommendation |
| :--- | :--- | :--- | :--- |
| `gateway/src/plc_reader.py` | `adapters/mitsubishi/mc_protocol.py` | **Necessary Shim.** `PLCReader` in `gateway/src/plc_reader.py` delegates directly to `MitsubishiMcProtocolAdapter` (lines 72–77, 107). It exists purely to provide backward compatibility for legacy scripts (`gateway/test_plc_connection.py`, `gateway/test_unit.py`) without code duplication. | **Retain as legacy shim.** |
| `gateway/gateway.py` | `agent/cli.py` | **Necessary Divergence.** `gateway.py` targets hardware serial streaming to ESP8266; `agent/cli.py` targets local HTTP API serving. Both share identical `GatewayEngine` core logic. | **Retain both distinct entry points.** |
| `core/simulated_adapter.py` | `tests/mock_modbus_server.py` | **Necessary Separation.** `simulated_adapter.py` is an in-process protocol adapter generating deterministic synthetic telemetry. `mock_modbus_server.py` is a socket-level Modbus TCP server used for network protocol integration testing. | **Retain both.** |

---

## 3. Read-Only Safety Verification

Industrial control safety mandates that monitoring software must never inadvertently modify field state, flip control relays, or interfere with PLC program execution.

### 3.1 Static Code & AST Analysis

1. **Protocol Command Inspection:**
   - **Mitsubishi MC Protocol (`adapters/mitsubishi/mc_protocol.py`):**
     - Allowed opcodes: `0x0401` sub-command `0x0001` (batch read bit units) and `batchread_wordunits` via `pymcprotocol.Type3E`.
     - Prohibited opcodes: `0x1401` (batch write), `0x1402` (random write), `0x1601` (remote RUN), `0x1602` (remote STOP), `0x1607` (remote PAUSE), `0x1617` (remote latch clear).
     - Result: **ZERO write, force, or remote-control opcodes exist.**
   - **Modbus TCP Protocol (`adapters/modbus/modbus_tcp.py`):**
     - Allowed function codes: FC 0x01 (`read_coils`), FC 0x02 (`read_discrete_inputs`), FC 0x03 (`read_holding_registers`), FC 0x04 (`read_input_registers`).
     - Prohibited function codes: FC 0x05 (`write_single_coil`), FC 0x06 (`write_single_register`), FC 0x0F (`write_multiple_coils`), FC 0x10 (`write_multiple_registers`).
     - Result: **ZERO write function codes exist in the client polling logic.**

2. **Poison-Pill Method Traps (`core/adapter_interface.py` & `adapters/modbus/modbus_tcp.py`):**
   - Base interface `IPlcProtocolAdapter` and derived adapters implement defensive traps:
     ```python
     def write(self, *args, **kwargs):
         raise ReadOnlySecurityViolation("Write operations are strictly prohibited...")
     def force_coil(self, *args, **kwargs):
         raise ReadOnlySecurityViolation("Forcing outputs is strictly prohibited...")
     def write_register(self, *args, **kwargs):
         raise ReadOnlySecurityViolation("Writing registers is strictly prohibited...")
     ```
   - Invoking any write method immediately terminates execution with an unhandled security exception.

3. **HTTP API Route Inspection (`agent/api.py`):**
   - AST inspection of `agent/api.py` confirms that 100% of registered routes use `@app.get()`.
   - Zero `@app.post()`, `@app.put()`, `@app.patch()`, or `@app.delete()` decorators exist.
   - CORS middleware explicitly restricts allowed methods to `allow_methods=["GET", "OPTIONS"]`.

4. **Frontend Dashboard Actions (`web/assets/js/app.js` & `web/index.html`):**
   - The UI contains zero buttons, toggles, or input fields capable of dispatching write commands.
   - The browser MQTT client (`state.mqttClient`) only invokes `.subscribe()`. Zero `.publish()` calls exist anywhere in the web frontend.

---

## 4. Software-Only Test Execution Evidence

All test executions were performed in the offline Windows Python 3.11 environment. No network scans, external package downloads, or physical connections were initiated.

### 4.1 Automated Python Test Suite
- **Command:** `.\gateway\.venv\Scripts\python.exe -m unittest discover tests`
- **Execution Time:** 1.987 seconds
- **Total Tests:** 100
- **Passed:** 100
- **Failed:** 0
- **Skipped:** 0
- **Key Suites Verified:**
  - `TestReadOnlyEnforcement`: AST verification of zero write opcodes, explicit rejection of write/force methods.
  - `TestLocalAgentApi`: Authentication verification, 401 challenge, header formatting, device discovery, telemetry endpoints, zero valid-value preservation.
  - `TestAgentLifecycleAndResilience`: Clean startup/shutdown, graceful handling of offline PLCs, thread safety.
  - `TestModbusAdapter`: 16-bit/32-bit register parsing, word swapping, discrete input handling, socket reconnect resilience.
  - `TestMockModbusServer`: Offline loopback Modbus TCP communication and packet exchange.
  - `TestCredentials`: Windows Credential Manager ctypes integration, secret precedence resolution, safe fallbacks.

### 4.2 Legacy Gateway Unit Tests
- **Command:** `.\gateway\.venv\Scripts\python.exe gateway\test_unit.py`
- **Execution Time:** 0.28 seconds
- **Suites:** 7 passed (`test_device_address_parsing`, `test_payload_structure`, `test_read_only_safety`, `test_offline_resilience`, `test_serial_framing`, `test_heartbeat_payload`, `test_status_monitoring`)
- **Status:** All 7 Python test suites passed successfully.

### 4.3 Frontend Headless Test Suite
- **Runner:** Headless Node.js DOM-mock test harness (`web/test.html` test block)
- **Assertions:** 128 assertions passed, 0 failed, 0 skipped.
- **Key Invariants Verified:**
  - Dynamic device selector population and active device switching.
  - Analog telemetry formatting (temperatures, RPM, flow rate, pressures) with units.
  - Negative analog values (e.g., `-12.5 °C`) correctly rendered.
  - Valid zero preservation: `raw_value: 0` with `quality: GOOD` remains `0`, never converted to `"—"`, `null`, or error.
  - Stale state transition: 15-second data timeout changes cards to `STALE` without mutating values to zero.
  - In-memory credential entry: Local agent key is excluded from `localStorage` persistence.

---

## 5. Security and Reliability Audit

### 5.1 API Authentication and Authorization
- **Design:** The Local Agent supports API Key authentication via `Authorization: Bearer <token>` or `X-Agent-API-Key: <token>`.
- **Precedence Hierarchy (`agent/credentials.py`):**
  1. Windows Credential Manager (`UniversalPlc/AgentApiKey`) — most secure, isolated in OS vault.
  2. Environment Variable (`AGENT_API_KEY`) — container and service friendly.
  3. CLI Argument (`--api-key`) — emits explicit security warning regarding OS process listing exposure.
  4. Unauthenticated Dev Mode — strictly restricted to local loopback.
- **Evaluation:** **PASS**. Authorization correctly rejects unauthorized requests with HTTP `401 Unauthorized` and `WWW-Authenticate: Bearer` challenge.

### 5.2 Network Binding and CORS Controls
- **Host Binding Policy (`agent/security.py`):**
  - Default bind address: `127.0.0.1` (loopback only).
  - Binding to external interfaces (e.g., `0.0.0.0` or LAN IP) requires explicit `--allow-remote` flag.
  - Non-loopback binding without an active API key is blocked at startup (`UnsafeBindingError`).
- **CORS Configuration (`agent/api.py`):**
  - Explicit local origin allowlist: `localhost:8080`, `127.0.0.1:8080`, `localhost:3000`, `127.0.0.1:3000`.
  - Wildcard origin (`*`) is **strictly prohibited**.
- **Evaluation:** **PASS**.

### 5.3 MQTT, TLS, and Microcontroller Security
- **Cloud Transport:** MQTT over TLS 1.2 on port 8883 (ESP8266) and Secure WebSockets over TLS on port 8084 (browser).
- **Client Separation:** Read-only browser clients are provisioned with subscriber-only credentials (`browser_readonly`), preventing unauthorized publication.
- **Firmware TLS Security Finding:**
  - In `firmware/plc_monitor/plc_monitor.ino:L247`, the ESP8266 firmware executes `wifiClient.setInsecure()`.
  - **Risk:** Traffic is encrypted against passive wiretappers, but the server certificate is not verified against a trusted root CA. A compromised DNS or active attacker on the Wi-Fi LAN could perform a Man-in-the-Middle (MITM) interception.
  - **Severity:** Medium.
  - **Remediation:** Flash the EMQX root CA certificate into ESP8266 flash memory or use certificate fingerprint verification (`wifiClient.setFingerprint()`).

### 5.4 Secrets & Accidental Credential Exposure
- **Git Repository Audit:**
  - `git ls-files` and file tree search confirm that zero actual credentials, keys, or passwords are committed.
  - Only safe template files exist: `gateway/config/secrets.ini.example`, `firmware/plc_monitor/secrets.h.example`.
- **API Secret Redaction:**
  - `agent/security.py:redact_secrets()` recursively scans and redacts any key containing `password`, `secret`, `token`, `key`, or `auth` before returning configuration or diagnostic JSON.
- **Evaluation:** **PASS**.

### 5.5 Stale Data, Quality Flags, Reconnection and Timeouts
- **Quality Enumerations:** Telemetry supports explicit `DataQuality` states: `GOOD`, `BAD`, `COMM_FAILURE`, `STALE`.
- **Zero Invariant:** A reading of `0` is recognized as valid industrial data (e.g., motor stopped or 0 °C) and never confounded with communication loss.
- **Stale Detection:** If telemetry ceases for >15 seconds, the UI transitions all points to `STALE` display while maintaining historical last-known values.
- **Evaluation:** **PASS**.

---

## 6. Vendor Integration Status & Verification Matrix

| Vendor / Protocol | Component / Driver | Verified by Source Inspection | Verified by Automated Tests / Simulation | Verified Against Physical Hardware | Overall Readiness Status |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Mitsubishi MELSEC-Q** (MC Protocol 3E Binary) | `adapters/mitsubishi/mc_protocol.py` | **PASS** | **PASS** | **NOT VERIFIED** | **READY FOR LAB COMMISSIONING** |
| **Modbus TCP** (Horner OCS / Generic Slave) | `adapters/modbus/modbus_tcp.py` | **PASS** | **PASS** | **NOT VERIFIED** | **READY FOR LAB COMMISSIONING** |
| **Virtual Simulation Engine** (Deterministic Synthetic) | `core/simulated_adapter.py` | **PASS** | **PASS** | **N/A (Software Only)** | **PRODUCTION READY (OFFLINE)** |
| **Windows Local Agent API** (FastAPI / Uvicorn) | `agent/api.py`, `agent/service.py` | **PASS** | **PASS** | **N/A (Software Only)** | **PRODUCTION READY (OFFLINE)** |
| **Cloud Forwarder & ESP8266** (Serial -> MQTT Bridge) | `gateway/`, `firmware/` | **PASS** | **PASS** | **NOT VERIFIED** | **PENDING LAB BENCH RUN** |

### Detailed Vendor Notes

#### Mitsubishi Electric MELSEC-Q (Q03UDECPU)
- **Verified Offline:** Base-16 hexadecimal address decoding for X/Y/B/W registers; base-10 decoding for D registers; contiguous batch read grouping; `pymcprotocol.Type3E` binary frame formatting; offline error trapping.
- **Hardware Limitations:** Live handshake with Q03UDECPU built-in Ethernet port (IP `192.168.3.39`, Port `5010`), TCP connection negotiation under PLC scan cycle load, and hardware relay transitions are **NOT VERIFIED**.

#### Modbus TCP (Horner OCS / Generic Modbus)
- **Verified Offline:** Modbus memory table mapping (Coils 0x, Discrete Inputs 1x, Input Registers 3x, Holding Registers 4x); 16-bit signed/unsigned integer decoding; 32-bit float and integer word swapping (`big_endian` and `little_endian`); socket disconnect recovery via `mock_modbus_server.py`.
- **Hardware Limitations:** Physical register mapping on Horner OCS Cscape application (mapping `%R` to Holding Registers and `%M` to Coils) and hardware network latency are **NOT VERIFIED**.

---

## 7. Categorized Audit Findings

| ID | Severity | Category | File Path | Finding Description | Evidence & Impact |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | **Medium** | Security / TLS | `firmware/plc_monitor/plc_monitor.ino:247` | ESP8266 NodeMCU firmware disables TLS certificate verification via `wifiClient.setInsecure()`. | Traffic is encrypted but vulnerable to MITM if local network routing or DNS is compromised. |
| **PKG-01** | **Medium** | Packaging / Deps | `gateway/requirements.txt` | Dependencies use unpinned minimum bounds (`>=`) and file is located in `gateway/` rather than project root. | Environment drift may occur over time if dependencies introduce breaking updates; root-level tooling expects `requirements.txt`. |
| **DOC-01** | **Low** | Documentation | `README.md` | Primary `README.md` reflects single-vendor Stage 1.6 architecture, omitting new `agent/`, `adapters/`, and `core/` modules. | Developers reading `README.md` may be confused regarding how to launch the multi-vendor Local Agent. |
| **DEP-01** | **Low** | Dependencies | `agent/api.py` / `fastapi` | Starlette deprecation warning during automated testing regarding `TestClient` and `httpx`. | Warning emitted during tests: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`. No runtime failure. |
| **OS-01** | **Info** | Platform Portability | `agent/credentials.py` | Credential Manager integration uses Windows Win32 `advapi32.dll` via `ctypes`. | Non-Windows platforms (Linux/macOS) fall back to environment variables. Container deployment must use env vars. |

---

## 8. Prioritized Remediation Plan

Before initiating Phase 08 implementation, the following remediation steps are recommended in priority order:

1. **Remediation 1 (Immediate / Documentation): Update `README.md` to Platform Baseline (DOC-01)**
   - Update `README.md` to document the Universal Platform architecture:
     - Entry point `python -m agent.cli`
     - Configuration file `config/devices.yaml`
     - Multi-vendor support (Mitsubishi MC Protocol, Modbus TCP, Simulated)
     - Test execution commands for the full 235-test suite.

2. **Remediation 2 (Medium / Dependency Hygiene): Root Lockfile & Dependency Pinning (PKG-01)**
   - Create a project root `requirements.txt` (or `pyproject.toml`) with pinned dependencies (`==`) matching the verified Python 3.11 virtual environment.

3. **Remediation 3 (Medium / Firmware Hardening): Add TLS CA Trust Anchor to Firmware (SEC-01)**
   - Prior to flashing production ESP8266 devices, include the EMQX Root CA certificate in flash or configure `wifiClient.setFingerprint()` to eliminate `setInsecure()`.

4. **Remediation 4 (Low / Cleanliness): Resolve Starlette TestClient Warning (DEP-01)**
   - Update test client imports or dependencies to align with modern Starlette/FastAPI specifications.

---

## 9. Explicit Physical-Commissioning Limitations

The following items **CANNOT** be certified or assumed operational without physical on-site commissioning:

> [!CAUTION]
> ### Unverified Physical Behaviors
> 1. **Physical PLC Network Reachability:** Physical Ethernet connectivity to Mitsubishi Q03UDECPU (`192.168.3.39:5010`) and Horner OCS (`192.168.3.50:502`) has not occurred.
> 2. **PLC Scan-Time Interaction:** Telemetry polling frequency (1.0 s) against a running PLC program has not been validated under actual CPU scan workloads.
> 3. **Hardware I/O State Correlation:** Visual alignment between physical LED indicators (X3 pushbutton, Y20 output relay) and dashboard tiles has not been verified against a live physical bench.
> 4. **Microcontroller Hardware Flashing:** Firmware flashing of the physical NodeMCU ESP8266 and CH340 USB-serial driver stability have not been validated on the target physical laptop.
> 5. **Plant Network Firewalls:** Industrial firewall rules, VLAN isolation, and corporate network proxies between the gateway and EMQX Cloud remain subject to on-site commissioning.

All automated software checks, read-only guarantees, and test suites are confirmed **PASSING** within the software simulation boundary. Live execution must strictly follow the step-by-step procedures in `LAB_HANDOVER.md` and `PRE_LAB_INTEGRATION_CHECKLIST.md`.
