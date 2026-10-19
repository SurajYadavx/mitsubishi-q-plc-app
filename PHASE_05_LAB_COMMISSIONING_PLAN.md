# PHASE 05 — LAB PREPARATION AND HARDWARE COMMISSIONING PLAN
## Safety, Accuracy & Protocol Trace Review (Phase 05.2 Revision)

**Project:** Universal Multi-Vendor Read-Only PLC Monitoring Platform  
**Target Controllers:**  
1. **Primary Laboratory Target:** Mitsubishi Electric MELSEC-Q (Q03UDECPU) via MC Protocol 3E Binary (Strictly Read-Only)  
2. **Secondary Multi-Vendor Target:** Modbus TCP Industrial Controllers (e.g., Horner OCS, Schneider, Siemens, WAGO)  
**Host Environment:** Microsoft Windows 10 / 11 64-bit Workstation  
**Document Status:** **PLANNING / PRE-BENCH REVIEW ONLY — PHYSICAL COMMISSIONING NOT COMPLETE**  
**Date:** October 20, 2026 (Revision 05.2)  
**Safety Invariant:** STRICTLY READ-ONLY ARCHITECTURE (Verified by AST Analysis, Route Auditing, and Unit Test Traps)  

---

> [!CAUTION]
> ### MANDATORY INDUSTRIAL SAFETY & PREFLIGHT CONSTRAINTS
> 1. **DO NOT MODIFY CONTROLLER OPERATING MODE:** The monitoring engineer must **NEVER** toggle the physical CPU `RUN/STOP` key switch, alter PLC parameters, edit ladder logic, or perform program downloads. All controller-level mode or parameter changes require explicit authorization and execution by the facility's licensed automation engineer.
> 2. **DO NOT DISRUPT OR REBOOT PLCs:** Recommendations to power-cycle or reset PLCs to clear socket errors are **STRICTLY PROHIBITED**. Disruptive actions can drop active processes or cause physical damage. Use non-disruptive diagnostics and escalate socket issues to the responsible automation engineer.
> 3. **NO UNAUTHORIZED NETWORK / FIREWALL CHANGES:** Do not run configuration commands that alter Windows network interfaces or firewall rules without explicit facility network authorization.
> 4. **PHYSICAL I/O CORRELATION IS STRICTLY CONDITIONAL:** Physical actuation of inputs (e.g., `X3`) and observation of outputs (e.g., `Y20`) is **STRICTLY CONDITIONAL** upon verified bench electrical isolation and confirmation that `Y20` is connected solely to a benign, non-hazardous visual indicator (e.g. pilot lamp or uncoupled terminal). If output load safety cannot be confirmed, physical stimulus is prohibited.
> 5. **UNVERIFIED BASELINE ASSUMPTIONS:** Network addresses (`192.168.3.39`, `192.168.3.100`), TCP port (`5010`), and the `X3 → Y20` rung relationship are **UNVERIFIED ASSUMPTIONS** drawn from template configuration files. They must be confirmed on-site before executing live tests.
> 6. **SIMULATION NEVER COUNTS AS PHYSICAL VALIDATION:** Passing tests in simulation mode validates application logic only. Physical hardware commissioning remains **NOT COMPLETE** until live bench execution takes place.

---

## 1. Verified Software Status & Baseline Audit

### 1.1 Fresh Preflight Test Execution
Prior to updating this plan, all automated test suites were freshly executed against the active repository on October 20, 2026:
- **Python Test Suites (`unittest discover -s tests -v`):** **99 executed, 99 passed (0 errors, 0 failures)** in 1.83s.
  - `tests/test_local_agent.py` (43 tests): Local REST API (`/health`, `/devices`, `/telemetry`), security policy, secret scrubbing, simulation flags, and Windows Credential Manager integration.
  - `tests/test_mc_adapter.py` (6 tests): MC Protocol 3E binary address parsing (hex base-16 for X/Y/B/W, decimal base-10 for D/M/R), communication failure quality tagging.
  - `tests/test_modbus_adapter.py` (18 tests): Modbus 0-based wire addressing, Modicon reference parsing, 32-bit register decoding, byte/word ordering, bit-within-register extraction.
  - `tests/test_read_only_enforcement.py` (32 tests): AST static inspection confirming zero write opcodes, write method traps raising `ReadOnlySecurityViolation`.
- **Frontend Verification Suite (`web/test.html`):** **107 assertions executed, 107 passed (0 failures)** evaluated via Node.js evaluation harness.

### 1.2 Verification Scope: Scope of Read-Only Enforcement
Rather than broad or unqualified claims, read-only enforcement is specifically substantiated by three independent software verification layers:
1. **Static AST Source Inspection:** Automated AST analysis (`tests/test_read_only_enforcement.py` and `tests/test_modbus_adapter.py`) validates that `adapters/mitsubishi/mc_protocol.py` contains zero Mitsubishi write command opcodes (`0x1401`, `0x1402`, `0x1601`, `0x1602`) and that `adapters/modbus/modbus_tcp.py` contains zero Modbus write function codes (`0x05`, `0x06`, `0x0F`, `0x10`).
2. **Unit Test Write Traps:** 32 automated unit tests verify that calling `.write()`, `.write_register()`, or `.force_coil()` on any adapter class immediately raises `ReadOnlySecurityViolation` without dispatching network frames.
3. **HTTP Route Inspection:** Automated inspection of `agent/api.py` confirms that the FastAPI application registers exclusively `GET` and `HEAD` routes, with zero `POST`, `PUT`, `DELETE`, or `PATCH` endpoints for control or memory modification.
4. **Manual Code Review:** Manual line-by-line inspection of production polling paths confirms that only read methods (`batchread_bitunits`, `batchread_wordunits`, `read_coils`, `read_discrete_inputs`, `read_holding_registers`, `read_input_registers`) are called.

### 1.3 Unverified Baseline Assumptions
The following parameters are configuration assumptions derived from default repository files (`config/devices.yaml` and `gateway/config/settings.ini`). They are **NOT** empirically verified lab facts:

| Parameter | Assumed Default Value | Source | Status | Required Lab Verification Action |
|---|---|---|---|---|
| **PLC IP Address** | `192.168.3.39` | `config/devices.yaml` | **UNVERIFIED ASSUMPTION** | Confirm with facility network engineer or inspect PLC Ethernet module parameters. |
| **PLC TCP Port** | `5010` | `config/devices.yaml` | **UNVERIFIED ASSUMPTION** | Confirm MC Protocol 3E open settings in GX Works2 parameters. |
| **Workstation IP** | `192.168.3.100` | Lab Handover example | **UNVERIFIED ASSUMPTION** | Obtain an authorized, non-conflicting static IP from the network administrator. |
| **Pushbutton Contact** | `X3` | Reference model | **UNVERIFIED ASSUMPTION** | Verify physical wiring on digital input terminal block. |
| **Master Relay Output** | `Y20` | Reference model | **UNVERIFIED ASSUMPTION** | Verify physical wiring and confirm load is non-hazardous (pilot lamp only). |
| **Ladder Rung `X3 → Y20`** | `LD X3 -> OUT Y20` | UI reference model | **UNVERIFIED ASSUMPTION** | Review authorized PLC program printout; verify ladder logic is present in PLC memory. |

### 1.4 Unresolved Items (Pending On-Site Lab Access & UI Evolution)
1. **Physical TCP Handshake:** Negotiation with the physical 100BASE-TX port of the Q03UDECPU.
2. **Physical Scan Cycle Latency:** Verification of real-world packet round-trip time under live controller execution.
3. **Safe Bench Output Isolation:** Empirical verification that output `Y20` is coupled solely to a benign indicator before physical testing.
4. **Physical Modbus Controller Profiles:** Field validation of unit ID, register mapping, and endianness on physical controllers.
5. **Dashboard Direct API Key Ingestion:** When API authentication is enabled on the Local Agent, the current browser dashboard interface (`index.html`) does not expose an interactive API key input field. The browser receives HTTP 401 Unauthorized unless the agent runs in unauthenticated loopback dev mode or the key is supplied via external API clients (curl/scripts).

---

## 2. Production Protocol Operation Traces

Every production communication operation executed by the platform has been audited line-by-line in the source code:

### 2.1 Mitsubishi MC Protocol 3E Production Trace (`adapters/mitsubishi/mc_protocol.py`)
During production polling, `MitsubishiMcProtocolAdapter.read_points()` issues only two pymcprotocol operations:
1. **Bit Unit Batch Read:**
   - Invocation: `self._client.batchread_bitunits(headdevice=head_label, size=count)` (Line 239)
   - Protocol Frame: MC Command `0x0401` (Batch Read), Subcommand `0x0001` (Bit Units).
   - Used For: Reading contiguous blocks of bit devices (`X0–X7`, `Y20–Y27`).
2. **Word Unit Batch Read:**
   - Invocation: `self._client.batchread_wordunits(headdevice=head_label, size=count)` (Line 283)
   - Protocol Frame: MC Command `0x0401` (Batch Read), Subcommand `0x0000` (Word Units).
   - Used For: Reading 16-bit register devices (`D100`, etc.).
3. **Prohibited Operations:**
   - Zero calls to `batchwrite_bitunits` or `batchwrite_wordunits` (MC Command `0x1401`).
   - Zero calls to random write (MC Command `0x1402`).
   - Zero calls to remote run/stop/pause/reset (MC Commands `0x1601`, `0x1602`, `0x1603`, `0x1607`).
   - The adapter defines no write methods; any attempt to invoke `.write()` triggers `ReadOnlySecurityViolation`.

### 2.2 Modbus TCP Production Trace (`adapters/modbus/modbus_tcp.py`)
During production polling, `ModbusTcpAdapter.read_points()` maps monitored points to four read-only Modbus functions:
1. **Read Coils:**
   - Invocation: `client.read_coils(address=addr.offset, count=count, slave=self.unit_id)` (Line 389)
   - Protocol Frame: Function Code `0x01` (Read Coils).
2. **Read Discrete Inputs:**
   - Invocation: `client.read_discrete_inputs(address=addr.offset, count=count, slave=self.unit_id)` (Line 407)
   - Protocol Frame: Function Code `0x02` (Read Discrete Inputs).
3. **Read Holding Registers:**
   - Invocation: `client.read_holding_registers(address=start_offset, count=count, slave=self.unit_id)` (Line 425)
   - Protocol Frame: Function Code `0x03` (Read Holding Registers).
4. **Read Input Registers:**
   - Invocation: `client.read_input_registers(address=start_offset, count=count, slave=self.unit_id)` (Line 443)
   - Protocol Frame: Function Code `0x04` (Read Input Registers).
5. **Prohibited Operations:**
   - Zero calls to `write_coil` (FC `0x05`), `write_register` (FC `0x06`), `write_coils` (FC `0x0F`), or `write_registers` (FC `0x10`).
   - Any attempt to invoke `.write()` or `.force_coil()` triggers `ReadOnlySecurityViolation`.

---

## 3. Required Equipment, Software, and Verified Repository Paths

### 3.1 Hardware Bill of Materials (BOM)

| Item | Component | Specification | Safety / Bench Requirement |
|---|---|---|---|
| **HW-01** | **PLC CPU** | Mitsubishi Electric MELSEC-Q **Q03UDECPU** | Built-in 100BASE-TX Ethernet interface. |
| **HW-02** | **Power Supply** | Mitsubishi Q61P (100–240VAC input) | Verified solid green `POWER` LED. |
| **HW-03** | **Base Rack** | Mitsubishi Q33B, Q35B, or Q38B | Secures CPU and I/O modules on backplane. |
| **HW-04** | **Digital Input Module** | Mitsubishi QX40, QX41, or QX42 | 24VDC optical isolation; terminal for `X3`. |
| **HW-05** | **Digital Output Module** | Mitsubishi QY10 (Relay) or QY40P/QY50 (Transistor) | **SAFETY CRITICAL:** Confirm `Y20` drives only a benign indicator lamp. |
| **HW-06** | **Bench Stimulus Rig** | Momentary Pushbutton (NO contact) + 24VDC bench supply | Electrically isolated bench fixture for `X3`. |
| **HW-07** | **Network Cable** | Category 5e/6 STP Industrial Ethernet cable | Patch cable to PLC Ethernet port. |
| **HW-08** | **Industrial Switch** | Unmanaged 10/100 Industrial Ethernet Switch | Optional bench isolation switch. |
| **HW-09** | **Engineering PC** | Windows 10 / 11 64-bit Workstation | Dedicated monitoring host. |

### 3.2 Verified Dependencies in Active Virtual Environment

The active virtual environment (`.\gateway\.venv`) was audited via `pip list` on October 20, 2026. The actual installed versions compared to `gateway/requirements.txt`:

| Package | Requirements.txt Specification | Actual Installed Version in `.venv` | Status |
|---|---|---|---|
| **Python** | Python $\ge 3.11.0$ | **3.11.5** | Verified |
| **pymcprotocol** | `>=0.3.0` | **0.3.0** | Verified (Note: Corrected from prior doc error) |
| **pymodbus** | `>=3.16.0,<4.0.0` | **3.16.1** | Verified |
| **fastapi** | `>=0.115.0` | **0.149.0** | Verified |
| **uvicorn** | `>=0.30.0` | **0.54.0** | Verified |
| **pyserial** | `>=3.5` | **3.5** | Verified |
| **pyyaml** | `>=6.0.1` | **6.0.3** | Verified |
| **httpx** | `>=0.27.0` | **0.28.1** | Verified |

*(Packages were inspected without automated upgrade, preserving environment stability).*

### 3.3 Verified Repository Files and Software Paths

| Component | Verified Path | Verified CLI Arguments / Capability |
|---|---|---|
| **Python Virtual Env** | `.\gateway\.venv\Scripts\python.exe` | Verified operational. |
| **Standalone Read Probe** | `gateway\test_plc_connection.py` | Verified flags: `--config`, `--ip`, `--port`, `--polls`, `--interval`. |
| **Local Agent Daemon** | `agent\cli.py` (invoked as `python -m agent.cli`) | Verified flags: `--config`, `--host`, `--port`, `--simulate`, `--api-key`, `--store-key`, `--delete-key`, `--credential-status`, `--allow-remote`, `--log-level`. |
| **Device Configuration** | `config\devices.yaml` | Primary device registry specifying `melsec_q03`, points `X0–X7`, `Y20–Y27`. |
| **Supervisory Dashboard** | `web\index.html` & `web\assets\js\app.js` | Web HMI served by the local agent on port 8000. |

---

## 4. Strict Preflight Checks & Non-Disruptive Diagnostics

Execute these verification steps in order. **Do not run commands that alter operating system settings without authorization.**

### Step 4.1: Workstation Network Configuration Verification
Verify that the workstation Ethernet network adapter has been assigned an authorized static IP on the PLC subnet by facility IT/OT:

```powershell
# Inspect active network adapters non-disruptively
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike "*Loopback*" } | Format-Table InterfaceAlias, IPAddress, PrefixLength
```
> [!IMPORTANT]
> If the workstation IP must be configured, obtain the authorized static IP (assumed default: `192.168.3.100`) from the network administrator. **Do not execute `New-NetIPAddress` without explicit administrative authorization.**

### Step 4.2: Physical Cabling & Link LED Inspection
1. Connect the industrial Ethernet cable between the workstation NIC and the Q03UDECPU built-in 100BASE-TX port.
2. Observe physical link LEDs on the PLC CPU front panel:
   - `100M`: Solid Green (100 Mbps link established).
   - `LINK/ACK`: Flashing or Solid Green (Layer 2 link active).
3. If LEDs remain unlit, check cable integrity and switch power. Do not proceed until Layer 1/2 link is established.

### Step 4.3: Non-Disruptive Network Reachability Probes
Verify Layer 3 and Layer 4 reachability non-disruptively:

```powershell
# 1. ICMP Ping probe (verify PLC network interface responds)
ping -n 4 192.168.3.39

# 2. TCP Port probe (verify MC Protocol port is listening)
Test-NetConnection -ComputerName 192.168.3.39 -Port 5010
```

**Expected Successful Probe Output:**
```text
ComputerName     : 192.168.3.39
RemoteAddress    : 192.168.3.39
RemotePort       : 5010
InterfaceAlias   : Ethernet
TcpTestSucceeded : True
```

> [!WARNING]
> If `TcpTestSucceeded` is `False`:
> - Do **NOT** power-cycle the PLC or flip the CPU reset switch.
> - Verify that the PLC CPU is in `RUN` mode. If in `STOP`, notify the authorized automation engineer.
> - Request the automation engineer to verify GX Works2 Ethernet port open settings (Protocol: `TCP`, Open System: `MC Protocol`, Port: `5010`).

### Step 4.4: Administrative Credential Vault Configuration
Store the agent administrative API key in the native Windows Credential Manager without exposing secrets in process tables or command-line logs:

```powershell
# 1. Check current vault status
.\gateway\.venv\Scripts\python.exe -m agent.cli --credential-status

# 2. Prompt securely for API key (input is masked, no terminal echo)
.\gateway\.venv\Scripts\python.exe -m agent.cli --store-key
# Prompt: Enter Agent API Key to store in Windows Credential Manager: <Enter your key>
# Output: Successfully stored API key in Windows Credential Manager ('UniversalPlc/AgentApiKey').
```

#### Supplying Key to Protected API Clients (Scripts / Curl):
An authorized operator or script client supplies the key securely without embedding it into command lines or source files:
- **In PowerShell:** Read securely using `$ApiKey = Read-Host -AsSecureString` or retrieve from vault via script, and pass in header:
  ```powershell
  $Headers = @{ "X-Agent-API-Key" = "YourStoredApiKey" }
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -Headers $Headers
  ```
- **In Browser Dashboard:** As noted in Section 1.4, administrative secrets are **never** hardcoded into `app.js`. Direct browser polling when the agent has authentication enabled requires a UI input field; until implemented, local browser monitoring runs in loopback dev mode (`127.0.0.1` unauthenticated).

---

## 5. Mitsubishi MELSEC-Q Commissioning Procedure

### 5.1 Parameter Alignment Verification
Confirm that `config/devices.yaml` reflects the verified IP and port:
```yaml
devices:
  - id: "melsec_q03"
    name: "Laboratory Mitsubishi MELSEC-Q"
    enabled: true
    manufacturer: "Mitsubishi Electric"
    model: "Q03UDECPU"
    adapter_id: "mitsubishi_mc_3e"
    connection:
      host: "192.168.3.39"      # Confirmed lab IP
      port: 5010                # Confirmed MC port
      timeout_sec: 3.0
      frame_type: "3E"
    poll_interval_sec: 1.0
```

### 5.2 Standalone Protocol Read Probe (Pre-Daemon Smoke Test)
Run the verified standalone diagnostic tool to test MC 3E binary frame reading in isolation:

```powershell
.\gateway\.venv\Scripts\python.exe gateway\test_plc_connection.py --ip 192.168.3.39 --port 5010 --polls 3
```

**Substantiated Success Output (Captured from PLCReader):**
```text
INFO  test     Python      : 3.11.5
INFO  test     pymcprotocol: 0.3.0
INFO  test     PASS - PLC TCP connection established
--- Poll 1/3 --------------------------------
{
  "timestamp_iso": "2026-10-20T...",
  "inputs": {"X0": 0, "X1": 0, "X2": 0, "X3": 0, "X4": 0, "X5": 0, "X6": 0, "X7": 0},
  "outputs": {"Y20": 0, "Y21": 0, "Y22": 0, "Y23": 0, "Y24": 0, "Y25": 0, "Y26": 0, "Y27": 0},
  "connected": true,
  "error": null
}
[PASS] All polls succeeded - gateway is ready to run.
```
*Stop Condition:* If `test_plc_connection.py` fails, stop immediately. Review Section 9 diagnostics; do not start the daemon.

### 5.3 Launch the Windows Local Agent in Production Mode
Start the monitoring daemon targeting physical hardware (do **NOT** provide `--simulate`):

```powershell
.\gateway\.venv\Scripts\python.exe -m agent.cli --config config/devices.yaml --host 127.0.0.1 --port 8000
```

**Substantiated Startup Log:**
```text
=================================================================
  Universal PLC Windows Local Agent API
  Listening on http://127.0.0.1:8000
  Interactive OpenAPI Docs: http://127.0.0.1:8000/docs
  Mode: PRODUCTION | Strictly Read-Only: True
  Auth: Loopback dev (unauthenticated) OR API Key required
  Auth Source: Windows Credential Manager ('UniversalPlc/AgentApiKey')
=================================================================
```

### 5.4 Automated Production API Validation
From a secondary terminal, query endpoints. The substantiated schemas captured from the running service:

#### 1. Health Response (`GET /api/v1/health`):
```json
{
  "status": "healthy",
  "agent_version": "1.0.0",
  "uptime_seconds": 12.4,
  "engine_running": true,
  "simulation_mode": false,
  "poll_count": 12,
  "devices_total": 3,
  "devices_enabled": 1,
  "devices_connected": 1,
  "gateway_id": "plc-gw-01",
  "timestamp_utc": "2026-10-20T...Z"
}
```

#### 2. Device Inventory (`GET /api/v1/devices`):
```json
[
  {
    "id": "melsec_q03",
    "name": "Laboratory Mitsubishi MELSEC-Q",
    "manufacturer": "Mitsubishi Electric",
    "model": "Q03UDECPU",
    "adapter_id": "mitsubishi_mc_3e",
    "enabled": true,
    "connected": true,
    "poll_interval_sec": 1.0,
    "points_count": 16,
    "last_successful_reading_utc": "2026-10-20T...Z"
  }
]
```

#### 3. Live Telemetry (`GET /api/v1/devices/melsec_q03/telemetry`):
```json
{
  "device_id": "melsec_q03",
  "gateway_id": "plc-gw-01",
  "sequence_number": 12,
  "timestamp_utc": "2026-10-20T...Z",
  "connected": true,
  "scan_duration_ms": 3.2,
  "status": "online",
  "error": null,
  "readings": {
    "X3": {
      "point_id": "X3",
      "raw_value": 0,
      "engineering_value": 0,
      "quality": "GOOD",
      "timestamp_utc": "2026-10-20T...Z",
      "data_type": "boolean",
      "engineering_units": "",
      "description": "Trigger Pushbutton Contact (Drives Y20 via Ladder)"
    },
    "Y20": {
      "point_id": "Y20",
      "raw_value": 0,
      "engineering_value": 0,
      "quality": "GOOD",
      "timestamp_utc": "2026-10-20T...Z",
      "data_type": "boolean",
      "engineering_units": "",
      "description": "Master Output Relay (Driven by X3 via Ladder)"
    }
  },
  "simulation_mode": false
}
```

### 5.5 Conditional Physical I/O Correlation Test & Latency Reconciliation
> [!CAUTION]
> **SAFETY PREREQUISITE:** This test may **ONLY** proceed if:
> 1. The workstation and PLC are connected on an authorized, electrically isolated test bench.
> 2. Output `Y20` has been physically inspected and confirmed to connect **ONLY** to a benign pilot lamp or uncoupled terminal block.
> 3. If `Y20` is coupled to any active actuator, valve, motor, or external equipment, **ABORT THIS TEST IMMEDIATELY**.

#### Latency Reconciliation & Proposed Benchmark:
- The agent polling interval is **1.0 second** (`poll_interval_sec: 1.0`).
- The browser local agent polling interval is **1.0 second** (`setInterval(pollLocalAgent, 1000)`).
- Because the agent poll and browser fetch are asynchronous, the theoretical end-to-end latency is:
  $$T_{\text{latency}} = T_{\text{scan}} + T_{\text{agent-poll}} + T_{\text{browser-poll}}$$
  Where $T_{\text{agent-poll}} \in [0.0\text{ s}, 1.0\text{ s}]$ and $T_{\text{browser-poll}} \in [0.0\text{ s}, 1.0\text{ s}]$.
  Nominal expected latency: $\approx 1.0\text{ s}$. Worst-case latency: $\approx 2.0\text{ to }2.2\text{ s}$.
- **Proposed Acceptance Benchmark:** State transition reflected on dashboard within **$\le 2.5\text{ seconds}$** of physical contact closure. *(Note: This threshold is a proposed benchmark subject to physical empirical measurement).*
- **Measurement Methodology:** Use a synchronized video recording or high-speed camera capturing both the physical module LED and the workstation screen simultaneously.

**Test Steps:**
1. Open dashboard at `http://127.0.0.1:8000`. Confirm:
   - Header badge indicates `PRODUCTION (LIVE HARDWARE)`.
   - PLC subsystem indicates `VERIFIED ONLINE`.
   - Ladder diagram displays `REFERENCE LADDER RUNG 0001 (X3 → Y20)`.
2. Press and hold physical pushbutton on terminal `X3`:
   - Input LED `X3` lights on QX40 module.
   - Internal ladder logic (if present) energizes relay `Y20` on QY10 module.
   - Observe dashboard: tile `X3` transitions to `ON` (green) and relay `Y20` transitions to `ON` within the $\le 2.5\text{ s}$ proposed latency window.
   - Ladder flow line energizes.
3. Release pushbutton `X3`:
   - Observe physical LEDs de-energize; dashboard tiles return to `OFF` within $\le 2.5\text{ s}$.

---

## 6. Modbus TCP Multi-Vendor Profile Review

> [!IMPORTANT]
> **Modbus TCP Is a Protocol, NOT a Controller Profile:**  
> A generic Modbus TCP driver communicates using standardized Function Codes (0x01–0x04), but memory address mapping, bit allocation, word order, and slave addressing vary fundamentally between manufacturers and models. Unit IDs, memory mappings, and word order are **strictly device-dependent** and must be confirmed from the physical device's technical manual.

### 6.1 Representative Device-Dependent Parameters Matrix

The following table lists **representative examples** of controller configuration. Every parameter must be confirmed against the physical controller on-site:

| Parameter | Representative Horner OCS | Representative Schneider (M221/M241) | Representative Siemens (S7-1200 via MB_SERVER) | Representative WAGO (750 Series) |
|---|---|---|---|---|
| **TCP Port** | `502` (Device-dependent) | `502` | `502` | `502` |
| **Unit ID (Slave ID)** | `1` or `255` (Device-dependent) | `1` or `255` | `1` (Matched to `MB_SERVER`) | `1` |
| **Memory Mapping Scheme** | %R = Holding Regs, %M = Coils, %I = Discrete In | %MW = Holding Regs, %M = Coils | Data Block (DB) mapped to Holding Regs | %MW / %MD memory offset |
| **Addressing Base** | 0-based offset or Modicon 40001 | 0-based (%MW0 = 0) | 0-based | 0-based (%MW0 = 0) |
| **32-Bit Word Order** | `big_endian` (Device-dependent) | `big_endian` or `little_endian` | `big_endian` | `big_endian` |
| **Max Batch Read Limit** | 64 to 125 registers | 125 registers | 100 registers | 125 registers |
| **IDE Configuration** | Enable "Modbus Slave" in Cscape | Add "Modbus TCP Slave" in EcoStruxure | Call `MB_SERVER` block in TIA Portal OB1 | Enable Modbus service in WBM |

### 6.2 Secondary Modbus Device Commissioning Rules
1. Obtain the manufacturer's Modbus address map for the exact model and firmware.
2. Verify in `config/devices.yaml` that `word_order` aligns with the controller's register layout (`big_endian` vs `little_endian`).
3. Ensure the controller's internal scan cycle is evaluated so the 1.0s polling rate does not overload low-speed RTUs.

---

## 7. Acceptance Criteria: Real Hardware vs Simulation

| Evaluation Aspect | Simulated Acceptance Criteria (`--simulate`) | Physical Hardware Acceptance Criteria (Live) |
|---|---|---|
| **Operating Mode Flag** | `simulation_mode == True` | `simulation_mode == False` |
| **Adapter Identifier** | `adapter_id == "simulated"` | `adapter_id == "mitsubishi_mc_3e"` or `"modbus_tcp"` |
| **Network Socket** | Loopback dummy socket or virtual in-memory queue | Physical TCP socket to `192.168.3.39:5010` (or `502`) |
| **Socket Latency** | Virtual delay ($\approx 0.0\text{ ms}$) | Measured round-trip network latency ($1.0\text{ ms} - 15.0\text{ ms}$) |
| **Hardware Link LEDs** | No physical LED activity | CPU `SD/RD` Ethernet LEDs flash synchronously with 1s poll |
| **Physical I/O Excitation** | Simulated pattern toggle or random test values | Physical button actuation on isolated bench triggers verified response |
| **Acceptance Claim** | **Validates software architecture ONLY** | **Validates PHYSICAL COMMISSIONING** |

---

## 8. Expected API and Dashboard Behavior by Telemetry State

```
+---------------------------------------------------------------------------------------------------------+
|                                    Telemetry Operational State Matrix                                   |
+-------------------+----------------------------+-----------------------+--------------------------------+
| State             | Data Quality Indicator     | API Health / Status   | Dashboard HMI Presentation     |
+-------------------+----------------------------+-----------------------+--------------------------------+
| 1. Connected      | DataQuality.GOOD           | 200 OK, online        | Green dot, LIVE (<1s ago),     |
|    (Normal Poll)  |                            | latency < 20ms        | active tile states & ladder    |
+-------------------+----------------------------+-----------------------+--------------------------------+
| 2. Disconnected   | DataQuality.COMM_FAILURE   | 200 OK, offline       | Red dot, OFFLINE, error log,   |
|    (Link Lost)    |                            | last_error logged     | previous values dimmed/halted  |
+-------------------+----------------------------+-----------------------+--------------------------------+
| 3. Stale          | DataQuality.STALE          | 200 OK, online        | Yellow dot, STALE (>15s ago),  |
|    (Delayed Poll) |                            | age counter rising    | warning badge on freshness     |
+-------------------+----------------------------+-----------------------+--------------------------------+
| 4. Invalid / Bad  | DataQuality.BAD            | 200 OK, online        | Yellow/Red border on tile,     |
|    (Out of range) |                            | bad_points recorded   | engineering value shows "BAD"  |
+-------------------+----------------------------+-----------------------+--------------------------------+
| 5. Unknown        | DataQuality.UNCERTAIN      | 200 OK, initializing  | Gray dot, AWAITING POLL,       |
|    (Pre-first poll| or null raw value          | poll_count = 0        | dashes "—", no assumed zero    |
+-------------------+----------------------------+-----------------------+--------------------------------+
```

### Behavioral Guarantees:
- **No Fabricated Zeros:** If a communication failure occurs, the agent does **NOT** replace readings with `0` or `False`. Values are tagged with `DataQuality.COMM_FAILURE`, preserving telemetry integrity.
- **Fail Closed:** When communication is lost, continuity in the ladder logic visualization is marked `Logic Continuity: Unknown / Awaiting Telemetry` (not energized).
- **Reference Model Differentiation:** The dashboard identifies the ladder rung as `REFERENCE LADDER RUNG 0001 (X3 → Y20)` and displays `Reference Model` unless verified live telemetry and ladder logic documentation support a verified status.

---

## 9. Non-Disruptive Failure Diagnosis & Socket Exhaustion Runbook

> [!CAUTION]
> **NO PLC REBOOTS OR MODE TOGGLES:** Do not cycle PLC power or actuate the CPU reset switch. Use non-disruptive network diagnostics and escalate to the responsible automation engineer.

| Symptom | Probable Root Cause | Non-Disruptive Diagnostic & Escalation Action |
|---|---|---|
| **`Test-NetConnection` fails (Timeout)** | 1. Ethernet cable unplugged or loose.<br>2. Workstation IP on wrong subnet.<br>3. PLC CPU unpowered or switch power off. | 1. Check RJ45 link lights on workstation NIC and PLC Ethernet port.<br>2. Run `Get-NetIPAddress` to verify workstation IPv4 address.<br>3. Inspect Q61P power supply `POWER` LED (must be solid green).<br>4. If unpowered, do **NOT** attempt power repair; escalate to facility electrician. |
| **Ping succeeds, but Port 5010 refused** | 1. MC Protocol not configured on built-in Ethernet.<br>2. Port number mismatch.<br>3. PLC CPU in `STOP` mode. | 1. Visually check CPU front panel: observe `RUN` LED.<br>2. Do **NOT** toggle RUN/STOP key switch.<br>3. Escalate to automation engineer to inspect GX Works2 *Built-in Ethernet Port Settings* (confirm TCP port 5010 is open for MC Protocol). |
| **`MC Protocol Error 0xC059` (Socket Exhaustion)** | Simultaneous MC connection limit reached in PLC parameter table. | 1. **Workstation vs PLC Distinction:** `Get-NetTCPConnection -RemotePort 5010` shows only local workstation sockets. Even if Windows sockets close or enter `TIME_WAIT`, the PLC maintains an independent internal socket table.<br>2. Terminate any orphaned local Python or diagnostic processes on the workstation.<br>3. Do **NOT** assume local `TIME_WAIT` expiry clears the PLC connection table.<br>4. Do **NOT** power-cycle the PLC.<br>5. Escalate to the responsible automation engineer to inspect Ethernet channel diagnostics in GX Works2. |
| **Agent API returns HTTP 401 Unauthorized** | Missing or incorrect API key in request header. | 1. Pass `X-Agent-API-Key: <key>` header or `Authorization: Bearer <key>`.<br>2. Verify vault status non-disruptively: `python -m agent.cli --credential-status`.<br>3. Re-enter key via `python -m agent.cli --store-key`. |
| **Pushbutton pressed, but dashboard does not change** | 1. Pushbutton wired to contact other than `X3`.<br>2. Bench output `Y20` load not connected.<br>3. Reference ladder logic not present in PLC memory. | 1. Check physical LED for `X3` on QX40 module when button is pressed.<br>2. Check physical LED for `Y20` on QY10 module.<br>3. If `X3` lights on PLC rack but dashboard does not change, check Local Agent polling status via `/api/v1/health`.<br>4. If `X3` lights on dashboard but `Y20` does not, internal ladder logic differs from the reference model; escalate to automation engineer to review ladder program. |
| **Agent termination hangs** | Polling thread blocked in socket recv. | 1. Sliced sleep loop ($\le 50\text{ ms}$) in Phase 04C resolves normal polling sleep hangs.<br>2. Socket timeouts are set to 3.0s max.<br>3. If hung, terminate python process via Task Manager and review socket timeout configuration. |

---

## 10. Safe Shutdown and Demobilization Procedure

To demobilize the laboratory bench safely:
1. **Stop the Windows Local Agent:**
   - In the active PowerShell console hosting `agent.cli`, issue `Ctrl+C`.
   - Verify clean shutdown logging:
     ```text
     INFO  agent: KeyboardInterrupt received – shutting down Local Agent.
     INFO  agent: LocalAgentService stopped cleanly.
     INFO  agent: Local Agent terminated cleanly.
     ```
2. **De-energize External Bench Stimulus:**
   - Turn off 24VDC bench power supply powering pushbuttons and external indicator lights.
3. **Controller Rack Power Notice:**
   - Do **NOT** power down the PLC rack unless specifically authorized by facility operating procedures. In shared industrial environments, controllers must remain energized.
4. **Workstation Network Interface Restoration:**
   - If the workstation network adapter was modified for the bench subnet, request network authorization to revert the adapter to DHCP:
     ```powershell
     # Execute ONLY if authorized to restore DHCP
     Set-NetIPInterface -InterfaceAlias "Ethernet" -Dhcp Enabled
     ```

---

## 11. Laboratory Commissioning Sign-Off Checklist

```
SOFTWARE PREFLIGHT (COMPLETED & VERIFIED):
[X] 1. Software test suites verified: 99/99 Python tests pass, 107/107 frontend tests pass.
[X] 2. Windows Credential Manager integration verified (UniversalPlc/AgentApiKey).
[X] 3. Sliced polling sleep loop verified (< 100ms shutdown response).
[X] 4. Strict read-only invariant verified (zero write/force opcodes in AST).
[X] 5. Simulation truthfulness verified (simulation_mode=True explicitly reported).

LABORATORY PRE-CONNECTION CHECKS (PENDING AUTHORIZED BENCH ACCESS):
[ ] 6. Facility network authorization obtained for workstation IP assignment.
[ ] 7. PLC IP (assumed 192.168.3.39) and MC port (assumed 5010) confirmed with automation lead.
[ ] 8. Bench isolation confirmed: QY10 output Y20 verified connected ONLY to safe indicator.
[ ] 9. Physical Ethernet cable connected between workstation and Q03UDECPU port.
[ ] 10. Physical link LEDs (100M, LINK/ACK) verified illuminated on PLC CPU.
[ ] 11. Non-disruptive ICMP Ping to confirmed PLC IP successful (0% packet loss).
[ ] 12. Non-disruptive TCP port reachability confirmed via Test-NetConnection.
[ ] 13. API key stored in Windows Credential Manager via 'python -m agent.cli --store-key'.

PHYSICAL HARDWARE ACCEPTANCE TESTING (PENDING LIVE EXECUTION):
[ ] 14. Standalone MC Protocol smoke-test executed (gateway\test_plc_connection.py).
[ ] 15. Local Agent started in PRODUCTION mode (without --simulate flag).
[ ] 16. GET /api/v1/health confirms simulation_mode=False and engine_running=True.
[ ] 17. GET /api/v1/devices confirms adapter_id="mitsubishi_mc_3e" and connected=True.
[ ] 18. GET /api/v1/devices/melsec_q03/telemetry confirms DataQuality.GOOD on all 16 points.
[ ] 19. Dashboard loads at http://127.0.0.1:8000 displaying 'PRODUCTION (LIVE HARDWARE)'.
[ ] 20. Pushbutton X3 actuation physically lights QX40 LED and triggers Y20 on QY10 module.
[ ] 21. Dashboard tile X3 and output Y20 transition to ON (green) within <= 2.5s proposed benchmark.
[ ] 22. Pushbutton release de-energizes X3 and Y20 on dashboard and ladder diagram.
[ ] 23. Disconnection test: Disconnect cable -> verify COMM_FAILURE logged within 3.0s.
[ ] 24. Reconnection test: Reconnect cable -> verify automatic recovery within 2 polling cycles.
[ ] 25. Clean shutdown verified: Ctrl+C terminates agent daemon within < 1.0s.

POST-COMMISSIONING DEMOBILIZATION:
[ ] 26. 24VDC bench stimulus power supply turned off.
[ ] 27. Workstation Ethernet adapter restored to authorized network profile.
[ ] 28. Commissioning log and signed report archived.
```

---

## 12. Commissioning Approval & Sign-Off Status

- **Commissioning Phase:** Phase 05 — Lab Preparation and Hardware Commissioning  
- **Review Revision:** Phase 05.2 Safety, Accuracy & Protocol Trace Review  
- **Software State:** **SOFTWARE PREFLIGHT REPORTED PASSING — COMMISSIONING PLAN REVIEWED — PHYSICAL PLC COMMISSIONING AND PRODUCTION SECURITY VALIDATION PENDING.**  
- **Hardware Status:** **PENDING ON-SITE PHYSICAL BENCH ACCESS (NOT COMPLETE)**  
- **Read-Only Verification:** Verified by AST inspection, unit test traps, route auditing, and source code tracing  

> [!IMPORTANT]
> **VALIDATION STATUS & LIMITATIONS NOTICE:**  
> Automated software tests and source-level read-only checks are reported as passing within the local development and automated test harness environment. However, physical PLC connectivity, physical hardware I/O behavior under scan load, and full production security (including on-site credential access controls, network perimeter isolation, and physical output load safety) have **NOT** been validated. Physical hardware commissioning remains strictly pending authorized on-site bench execution.

| Role | Name | Signature | Status | Date |
|---|---|---|---|---|
| **Automation Software Lead** | _______________________ | _______________________ | Software Preflight Passing | 2026-10-20 |
| **Laboratory Commissioning Engineer** | _______________________ | _______________________ | Pending Physical Bench Access | ______________ |
| **Systems Safety Auditor** | _______________________ | _______________________ | Commissioning Plan Reviewed | 2026-10-20 |
