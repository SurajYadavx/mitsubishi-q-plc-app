# PHASE 04A — VERIFICATION AUDIT REPORT (PRE-PHASE 04B)

**Universal Multi-Vendor Read-Only PLC Monitoring Platform**  
**Component:** Phase 04A Windows Local Agent Verification Audit  
**Date:** October 2026  
**Auditor:** Senior OT Cybersecurity & Software Architecture Auditor  
**Audit Scope:** Credential Handling, API Security, Browser Integration Feasibility, Test Verification  
**Decision:** ⚠️ **CONDITIONAL GO FOR PHASE 04B** (Specific gating conditions detailed below)  

---

## 1. Executive Summary

A comprehensive, evidence-based verification audit of the Phase 04A Windows Local Agent was conducted on the actual repository code and runtime environment. No new features, protocol adapters, or UI modifications were introduced.

The audit verified that the core monitoring foundation is strictly read-only, loopback host binding enforcement functions as designed, recursive secret redaction effectively sanitizes diagnostic endpoints, and background polling is cleanly decoupled from HTTP requests. All 77 platform test cases and 7 legacy test suites pass with zero regressions.

However, three significant architectural and security findings were identified that must be addressed prior to or during Phase 04B:
1. **Windows Credential Manager / OS Vault is MISSING**: API keys and connection credentials reside solely in configuration files or CLI memory; no integration with Windows Credential Manager (DPAPI / `win32cred` / `keyring`) exists.
2. **Cross-Origin Browser Integration from Public HTTPS is BLOCKED**: The hosted dashboard at `https://surajyadavx.github.io/` cannot communicate directly with the local agent at `http://127.0.0.1:8000` due to W3C Active Mixed Content blocking and Chromium Private Network Access (PNA) restrictions. CORS alone cannot resolve this.
3. **CORS Headers are Currently ABSENT**: The FastAPI service lacks `CORSMiddleware`; browser preflight `OPTIONS` requests receive HTTP 405 Method Not Allowed.

---

## 2. Check-by-Check Audit Matrix

| Audit Area | Item / Check | Status | Evidence / Reference |
|---|---|---|---|
| **Audit A** | OS-Backed Credential Vault (Windows Credential Manager / DPAPI) | ❌ **FAIL (MISSING)** | Grep across repository confirmed 0 references to `win32cred`, `keyring`, or `CryptProtectData`. API keys passed via CLI `--api-key` and stored in plain memory strings. |
| **Audit A** | Device-Detail Endpoint Credential Leakage | ✅ **PASS** | `agent/service.py` L259 routes `dev.connection` through `redact_secrets()`. Tested in `test_device_detail_endpoint_and_secret_redaction`. |
| **Audit A** | Configuration-Status Endpoint Credential Leakage | ✅ **PASS** | `agent/service.py` L369-378 strips all connection parameters, publishing only non-sensitive device metadata (`id`, `name`, `adapter_id`, `enabled`, `points_count`). |
| **Audit A** | Diagnostics Endpoint Credential Leakage | ✅ **PASS** | `agent/service.py` L327 & L343 recursively redacts `adapter_diag` and `dev.connection`. |
| **Audit A** | Exception Handler Credential Leakage | ✅ **PASS** | `agent/api.py` L184-190 intercepts unhandled exceptions, logging internally while masking HTTP responses with generic `"Internal server error occurred."` |
| **Audit A** | Logging Paths Credential Leakage | ✅ **PASS** | `agent/cli.py` L135 logs `"Auth: API Key required"`, never outputting the raw key. Polling logs do not log passwords. |
| **Audit A** | Repository Secret Hygiene | ✅ **PASS** | Only `.example` templates tracked in Git. `.gitignore` excludes `secrets.ini`, `secrets.h`, `*.key`, `*.pem`, `*.crt`. All documented keys are test tokens. |
| **Audit B** | Default Host Binding Security | ✅ **PASS** | `agent/cli.py` defaults to `127.0.0.1`. `validate_host_binding("0.0.0.0", allow_remote=False)` raises `UnsafeBindingError`. |
| **Audit B** | Remote Binding Enforcement | ⚠️ **PASS (ADVISORY)** | Binding to non-loopback requires `--allow-remote`, but `--allow-remote` does NOT currently mandate that `--api-key` is configured. |
| **Audit B** | API Key Authentication | ✅ **PASS** | Missing key returns HTTP 401; invalid key returns HTTP 401; valid `X-Agent-API-Key` and `Authorization: Bearer` return HTTP 200. |
| **Audit B** | CORS Configuration | ❌ **FAIL (ABSENT)** | `agent/api.py` has no `CORSMiddleware`. `OPTIONS` requests return HTTP 405 with no `Access-Control-*` headers. |
| **Audit B** | Strictly Read-Only Invariant | ✅ **PASS** | 100% of exposed routes are HTTP `GET`. Static AST inspection verified zero write opcodes in Mitsubishi and Modbus drivers. |
| **Audit B** | Device ID Input Sanitization | ✅ **PASS** | Regex `^[a-zA-Z0-9_\-]{1,64}$` rejects invalid characters, path traversal, and injections with HTTP 400. |
| **Audit B** | Unknown Device Handling | ✅ **PASS** | Non-existent device IDs safely return HTTP 404 with structured error JSON. |
| **Audit C** | HTTPS Browser Integration Feasibility | ❌ **FAIL (BLOCKED)** | W3C Active Mixed Content blocks HTTPS (`surajyadavx.github.io`) from querying HTTP loopback (`127.0.0.1:8000`). |
| **Audit D** | Platform Test Suite Execution | ✅ **PASS** | `unittest discover -s tests -v`: 77 tests passed in 5.500s. Exit code 0. |
| **Audit D** | Legacy Gateway Test Suite Execution | ✅ **PASS** | `gateway/test_unit.py`: 7 suites passed. Exit code 0. |
| **Audit D** | Live Simulation Smoke Test | ✅ **PASS** | Smoke test verified valid zero distinction (`quality=GOOD`), sub-millisecond response, and 0 extra PLC polls over 30 rapid hits. |
| **Audit D** | Physical PLC Hardware Operations | ⚠️ **NOT VERIFIED** | Physical Ethernet link to Q03UDECPU (`192.168.3.39:5010`) and live Modbus TCP slave remain unverified without physical hardware. |

---

## 3. Detailed Audit Findings

### Audit A — Credential Handling & Storage

#### 1. OS-Backed Credential Vault Status: MISSING
- **Requirement Analysis**: The Phase 01 architecture audit conceptualized a hardware-bound identity and OS credential vault (Windows DPAPI / Windows Credential Manager / Linux Secret Service) for storing API keys and gateway identity.
- **Actual Implementation**: No OS-backed vault integration exists. The Windows Local Agent currently accepts an API key via the command-line argument `--api-key <token>`. The key is retained in process memory within `SecurityPolicy.api_key`.
- **Finding**: **MISSING**. If Windows Credential Manager persistence is required for unattended service startup, a module utilizing Python's `keyring` library or `ctypes` calling `win32cred.dll` must be added in Phase 04B.

#### 2. Redaction and Leakage Path Verification: PASS
Five critical data paths were inspected for credential leakage:
1. **`GET /api/v1/devices/{device_id}`**: `agent/service.py` L259 wraps `dev.connection` in `redact_secrets()`. Any key containing `password`, `secret`, `token`, `api_key`, `credential`, or `auth` is masked with `[REDACTED]`.
2. **`GET /api/v1/config/status`**: The configuration response strips connection dictionaries completely, exposing only `id`, `name`, `adapter_id`, `enabled`, and `points_count`.
3. **`GET /api/v1/devices/{device_id}/diagnostics`**: `adapter_diagnostics` and `connection_summary` are passed through `redact_secrets()`.
4. **Exception Handling**: Unhandled exceptions in `agent/api.py` are caught by `generic_exception_handler`, which logs the traceback to the internal logger and returns a generic `{"error": True, "detail": "Internal server error occurred."}` without exposing internal paths or connection strings to callers.
5. **Logging Output**: The CLI logger reports `"Auth: API Key required"`, never logging the raw key string.

#### 3. Repository Secret Hygiene: PASS
- `git status --short` and `git ls-files` confirm that no live credentials, `.env` files, `secrets.ini`, or `secrets.h` are tracked in the repository.
- Example files (`gateway/config/secrets.ini.example` and `firmware/plc_monitor/secrets.h.example`) contain explicit placeholder strings (`your_wifi_password_here`).
- Documented keys in test files (`AuditVerificationKey-2026`, `StationApiKeyXYZ`) are explicit test-only fixtures.

---

### Audit B — API Security & Network Isolation

#### 1. Host Binding & Default Loopback: PASS
- By default, `agent/cli.py` binds to `127.0.0.1`.
- `validate_host_binding()` enforces that only `127.0.0.1`, `localhost`, and `::1` are accepted without explicit opt-in.
- Supplying `--host 0.0.0.0` or `--host 192.168.1.10` without `--allow-remote` immediately raises `UnsafeBindingError` and aborts process launch (exit code 1).

#### 2. Remote Binding Safety Advisory: PASS WITH ADVISORY
- **Advisory Finding**: If `--allow-remote` is passed, the CLI issues a warning:
  ```
  WARNING: Binding Local Agent to non-loopback interface '0.0.0.0'. Ensure firewall rules and API authentication are configured.
  ```
  However, the CLI does **not** enforce that `--api-key` is supplied when `--allow-remote` is set. An operator could run `python -m agent.cli --host 0.0.0.0 --allow-remote` and inadvertently expose the unauthenticated agent to the local subnet.
- **Recommended Hardening for Phase 04B**: Make `--api-key` mandatory whenever `--allow-remote` is specified.

#### 3. API Key Authentication: PASS
Verification executed via `TestClient`:
- `GET /api/v1/health` without key &rarr; `HTTP 401 Unauthorized` (`detail: "Unauthorized: Valid API key required."`)
- `GET /api/v1/health` with `X-Agent-API-Key: WrongKey` &rarr; `HTTP 401 Unauthorized`
- `GET /api/v1/health` with `X-Agent-API-Key: AuditVerificationKey-2026` &rarr; `HTTP 200 OK`
- `GET /api/v1/health` with `Authorization: Bearer AuditVerificationKey-2026` &rarr; `HTTP 200 OK`

#### 4. CORS Configuration: FAIL (ABSENT)
- The FastAPI application in `agent/api.py` does not include `CORSMiddleware`.
- An `OPTIONS` preflight request returns `HTTP 405 Method Not Allowed` with no `Access-Control-Allow-Origin` headers.

#### 5. Strictly Read-Only Invariant: PASS
- All exposed routes in `agent/api.py` are registered via `@app.get`.
- Automated test `test_strict_read_only_no_write_routes` iterates over `app.routes` and verifies that zero `POST`, `PUT`, `DELETE`, or `PATCH` routes exist.
- Protocol adapters implement strict read-only traps: calling `write()`, `write_register()`, or `force_coil()` immediately raises `ReadOnlySecurityViolation`.

#### 6. Input Sanitization & Error Handling: PASS
- `validate_device_id()` restricts device IDs to `^[a-zA-Z0-9_\-]{1,64}$`.
- URL encoded traversal (`invalid..id`) and injection syntax (`plc;drop table`) return `HTTP 400 Bad Request`.
- Unknown valid device IDs (`nonexistent_plc_99`) return `HTTP 404 Not Found`.

---

### Audit C — Browser Integration Feasibility Analysis

#### Scenario
- **Hosted Frontend Dashboard**: `https://surajyadavx.github.io/mitsubishi-q-plc-app/` (Origin: `https://surajyadavx.github.io`, Public Network Zone)
- **Target Local Agent**: `http://127.0.0.1:8000` (Origin: `http://127.0.0.1:8000`, Loopback Network Zone)

#### Feasibility Assessment: BLOCKED BY BROWSER SECURITY ARCHITECTURE

Can a browser page on GitHub Pages reliably call `http://127.0.0.1:8000`? **NO.**  
Relying on direct cross-origin HTTP calls from a public HTTPS web page to a local HTTP loopback daemon fails due to three fundamental browser security barriers:

```
  ┌───────────────────────────────────────────────┐
  │  Browser Context: https://surajyadavx.github.io │
  │          (Secure Public Web Origin)           │
  └───────────────────────┬───────────────────────┘
                          │
                          │ fetch("http://127.0.0.1:8000/api/v1/telemetry")
                          ▼
            ❌ BARRIER 1: Active Mixed Content
            (Insecure HTTP blocked from Secure HTTPS)
                          │
                          ▼
            ❌ BARRIER 2: Private Network Access (PNA)
            (Public origin blocked from querying Loopback)
                          │
                          ▼
            ❌ BARRIER 3: CORS (Absent on Agent)
            (No Access-Control-Allow-Origin header)
                          │
                          ▼
  ┌───────────────────────────────────────────────┐
  │       Target: http://127.0.0.1:8000/api/v1    │
  │              (Local Agent Daemon)             │
  └───────────────────────────────────────────────┘
```

1. **Active Mixed Content Blocking**:
   - Modern browsers (Chrome, Edge, Firefox, Safari) strictly enforce the W3C Mixed Content specification.
   - Any subresource fetch (`fetch()`, `XMLHttpRequest`, `WebSocket` `ws://`) from a page loaded over HTTPS to an insecure `http://` endpoint is classified as **Active Mixed Content** and blocked at the network layer prior to transmission.
   - The browser console displays: `Mixed Content: The page at 'https://...' was loaded over HTTPS, but requested an insecure resource 'http://127.0.0.1:8000/...'. This request has been blocked; the content must be served over HTTPS.`
2. **Private Network Access (PNA) / Local Network Access (LNA)**:
   - Chromium security policy restricts requests from public web origins (`github.io`) to private IP ranges or loopback (`127.0.0.1`).
   - PNA mandates an explicit preflight request with `Access-Control-Request-Private-Network: true`. The server must reply with `Access-Control-Allow-Private-Network: true`.
   - Furthermore, Chromium requires the target private endpoint to be a **Secure Context** (HTTPS). An insecure `http://127.0.0.1` target will fail PNA preflight checks when called from public HTTPS.
3. **CORS is Insufficient**:
   - Adding `Access-Control-Allow-Origin: *` to the local agent **cannot** bypass Active Mixed Content blocking or PNA secure-context requirements.
4. **TLS / HTTPS on Loopback is Operationally Problematic**:
   - Generating a self-signed HTTPS certificate for `127.0.0.1` causes browsers to display certificate authority warnings (`ERR_CERT_AUTHORITY_INVALID`).
   - Browsers do not allow background API calls to endpoints with untrusted TLS certificates without manual security exceptions. Installing a custom root CA (e.g. via `mkcert`) into the Windows Certificate Store is excessively complex for end users.

#### Recommended Architecture for Local Monitoring
To achieve 100% reliable local monitoring without fighting browser security policies:

| Option | Architecture | Mixed Content Block? | PNA Block? | Viability |
|---|---|---|---|---|
| **Option 1 (Recommended)** | **Local Static Hosting**: Serve the web UI directly from the Local Agent (`http://127.0.0.1:8000/`) or via local dev server (`http://localhost:8080`). | ❌ No (Both are loopback HTTP) | ❌ No (Loopback to Loopback) | **100% Viable & Turnkey** |
| **Option 2** | **Lightweight Desktop Wrapper**: Package the frontend dashboard using Tauri, Electron, or a native WebView pointing to the local agent. | ❌ No (Local application scheme) | ❌ No | **100% Viable** |
| **Option 3** | **Cloud MQTT Bridge (Status Quo)**: The hosted GitHub Pages dashboard continues subscribing to the EMQX Cloud MQTT broker over `wss://` (Secure WebSockets). | ❌ No (Both are HTTPS/WSS) | ❌ No (Both are public) | **100% Viable** |
| **Option 4 (Flawed)** | Public HTTPS page (`github.io`) fetching `http://127.0.0.1:8000`. | 🛑 **BLOCKED** | 🛑 **BLOCKED** | **NON-VIABLE** |

**Strategic Guidance**: The public GitHub Pages dashboard should remain paired with the cloud MQTT broker (`wss://`). The Windows Local Agent REST API should serve local frontends (either served directly by the agent or run locally via `http://localhost:8080`).

---

### Audit D — Test Verification & Empirical Results

#### 1. Platform Discovery Test Suite
```powershell
.\gateway\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
- **Exit Code**: `0`
- **Total Tests**: `77`
- **Failures**: `0`
- **Errors**: `0`
- **Execution Time**: `5.500s`
- **Verified Components**:
  - Core adapter interface, simulated PLC adapter, and dynamic registry: 33 tests pass.
  - Modbus TCP address parser, register decoders, quality mapping, and read-only enforcement: 23 tests pass.
  - Windows Local Agent host binding, secret redaction, background engine lifecycle, 7 REST endpoints, 404/400 handling, and API key auth: 21 tests pass.

#### 2. Legacy Gateway Regression Suite
```powershell
.\gateway\.venv\Scripts\python.exe gateway/test_unit.py
```
- **Exit Code**: `0`
- **Output**: `All 7 Python test suites passed successfully!`

#### 3. Simulation API Smoke Test & Duplicate Polling Verification
Executed via `verify_smoke_simulation.py`:
- **Valid Zero Distinction**: Confirmed that Point `X0` through `X2` and `Y21` through `Y27` report `raw_value=0`, `engineering_value=0`, and `quality="GOOD"`. Valid zero readings are never conflated with missing or failed telemetry (`COMM_FAILURE`).
- **Duplicate Polling Prevention**: Executed 30 rapid HTTP requests against `/api/v1/devices/melsec_q03/telemetry` within 110ms. The background engine poll counter advanced by **0 cycles**. Telemetry is served purely from the thread-safe in-memory cache without generating duplicate protocol scans.

#### 4. Physical Hardware Boundary
- **Status**: **NOT VERIFIED (Physical Hardware Required)**
- Software protocols, binary encoders, and simulated models are 100% verified.
- Live physical Ethernet communication with Mitsubishi Q03UDECPU (`192.168.3.39:5010`) and physical Modbus field controllers remains unverified in this software environment.

---

## 4. Discrepancies Between Previous Report and Codebase

| Area | Implementation Report Claim | Actual Code State | Severity |
|---|---|---|---|
| **OS Credential Vault** | Report Section 5 stated "Configuration and credential-storage behavior: live credentials stay in memory for process lifecycle". | Windows Credential Manager / DPAPI vault is **MISSING**. Keys must be passed via CLI flag `--api-key`. | Moderate |
| **CORS Support** | Report Section 10 recommended CORS as future Phase 04B scope. | CORS is currently **ABSENT**, causing browser `OPTIONS` preflight requests to fail with HTTP 405. | Moderate |
| **Remote Binding Security** | Report Section 4 documented that non-loopback bindings require `--allow-remote`. | True, but `--allow-remote` does not enforce that `--api-key` is configured, allowing accidental unauthenticated network exposure. | Low / Advisory |

---

## 5. Outstanding Security & Architecture Risks

1. **Unauthenticated Remote Binding Risk**: Passing `--allow-remote` without `--api-key` exposes all PLC telemetry to the local network without authentication.
2. **Public-Origin HTTPS Browser Incompatibility**: Direct integration between `https://surajyadavx.github.io` and `http://127.0.0.1:8000` will fail due to Mixed Content and Private Network Access restrictions.
3. **In-Memory Credential Persistence**: When the agent is restarted, `--api-key` must be re-supplied on the command line because no persistent OS vault stores it.

---

## 6. Concise GO / NO-GO Recommendation for Phase 04B

### Decision: ⚠️ **CONDITIONAL GO FOR PHASE 04B**

The core platform architecture is stable, clean, strictly read-only, and fully verified across 77 automated tests. Phase 04B may proceed **provided that the following gating tasks are included in the Phase 04B implementation scope**:

1. **Enforce Authentication on Remote Binding**: Modify `agent/cli.py` so that specifying `--allow-remote` requires `--api-key` (cannot bind to `0.0.0.0` unauthenticated).
2. **Local Web UI Serving**: Add static file mounting in `agent/api.py` (`app.mount("/", StaticFiles(directory="web", html=True))`) so the monitoring dashboard can be accessed directly from `http://127.0.0.1:8000/` without triggering browser Mixed Content blocks.
3. **Configurable CORS Middleware**: Add FastAPI `CORSMiddleware` with explicit local origin whitelisting (`http://localhost:8080`, `http://127.0.0.1:8080`).
4. **Real-Time Telemetry Streaming**: Implement Server-Sent Events (SSE) `/api/v1/devices/{device_id}/telemetry/stream` for low-latency browser updates.
5. **Windows Credential Manager Vault (Optional/Deferred)**: Add an OS-backed vault helper using `keyring` or Windows DPAPI if unattended credential persistence is required.
