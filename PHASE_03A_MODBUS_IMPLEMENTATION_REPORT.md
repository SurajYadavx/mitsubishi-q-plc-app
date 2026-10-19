# PHASE 03A — MODBUS TCP READ-ONLY ADAPTER IMPLEMENTATION REPORT

**Universal PLC Monitoring Platform Migration**  
**Document Status:** Complete  
**Date:** 2026-10-19  
**Engineer:** Senior Industrial Automation Software Architect & Protocol Engineer  
**Scope:** Phase 03A (Modbus TCP Read-Only Adapter & Deterministic Integration Test Harness)

---

## Executive Summary

Phase 03A extends the multi-vendor **Universal PLC Monitoring Platform** (established in Phase 02) with a production-grade, strictly read-only **Modbus TCP Protocol Adapter** (`adapters/modbus/modbus_tcp.py`). 

The adapter conforms to the `IPlcProtocolAdapter` interface, supports all four primary Modbus read function codes (FC 0x01, 0x02, 0x03, 0x04), and provides dual-format addressing (0-based protocol offsets and industry-standard 1-based Modicon 5/6-digit notation). It strictly enforces OT read-only security invariants at the Python API level, internal opcode dispatch level, and network traffic level.

All 48 test cases across the unified test suite passed in 0.37 seconds, all 7 legacy test suites passed without deviation, and the existing Mitsubishi Q-series monitoring workflow remains 100% backward compatible.

---

## 1. Files Added and Modified

### Files Added:
1. **`adapters/modbus/__init__.py`**
   - Package initialization for the Modbus adapter family.
   - Exports `ModbusTcpAdapter`, `parse_modbus_address`, and `ModbusTable`.

2. **`adapters/modbus/modbus_tcp.py`**
   - Complete implementation of `ModbusTcpAdapter` conforming to `IPlcProtocolAdapter`.
   - Modbus address parser supporting prefix notation (`coil:`, `di:`, `hr:`, `ir:`) and Modicon 5/6-digit notation (`00001`, `10001`, `30001`, `40001`).
   - Bit-within-register extraction (e.g., `hr:0.3` or `40001.3`).
   - 16-bit signed/unsigned and 32-bit integer/float decoding with configurable word order (`big_endian`, `little_endian` / `word_swap`).
   - Strict read-only enforcement: `write()`, `force_coil()`, and `write_register()` raise `ReadOnlySecurityViolation`.
   - Comprehensive error, timeout, protocol exception, and disconnection handling returning normalized telemetry batches.

3. **`tests/mock_modbus_server.py`**
   - Deterministic in-process Modbus TCP server running on an OS-assigned ephemeral loopback port (`127.0.0.1`).
   - Full support for FC 0x01 (Read Coils), FC 0x02 (Read Discrete Inputs), FC 0x03 (Read Holding Registers), and FC 0x04 (Read Input Registers).
   - Write trapping: intercepting any FC 0x05, 0x06, 0x0F, or 0x10 and recording write attempts to `write_requests_received`.
   - Injected failure capabilities (`simulate_drop`, `exception_codes`) to test timeout and broken-pipe behavior deterministically without external brokers or real PLCs.

4. **`tests/test_modbus_adapter.py`**
   - 15 comprehensive unit and integration tests covering:
     - Address parsing (prefixes, Modicon notation, bit indexing, syntax validation).
     - All 4 function codes with known values.
     - Differentiating valid zero values (`0`) from communication failures (`DataQuality.COMM_FAILURE`).
     - Signed INT16 (negative numbers), unsigned UINT16, UINT32, INT32, FLOAT32, and word swap.
     - Single-bit extraction from holding registers.
     - Linear engineering scaling and offset.
     - Modbus protocol exceptions (e.g., Exception Code 0x02 Illegal Data Address).
     - Network disconnection / server drop handling.
     - Read-only security traps (method-level exceptions and mock server write verification).
     - Multi-vendor gateway integration (Simulated PLC + Modbus device polling simultaneously with failure isolation).

### Files Modified:
1. **`core/adapter_registry.py`**
   - Registered `modbus_tcp` factory in `AdapterRegistry` and default global instance.

2. **`core/config_loader.py`**
   - Updated address validation logic to recognize Modbus address formats (`coil:`, `di:`, `hr:`, `ir:`, and 5/6-digit Modicon strings) when validating device configurations.

3. **`config/devices.yaml`**
   - Added a documented, schema-compliant `modbus_cell_01` configuration example demonstrating connection parameters, word order, and point definitions across all 4 Modbus memory tables.

4. **`gateway/requirements.txt`**
   - Pinned `pymodbus>=3.16.0,<4.0.0` and `pyyaml>=6.0.1`.

---

## 2. Dependency and Version Choice with Rationale

- **Library Selected:** `pymodbus==3.16.1`
- **Installation Location:** Global environment and `gateway/.venv`.

### Rationale:
1. **Standard & Maintenance:** `pymodbus` is the de facto standard, active, Python Software Foundation-recommended Modbus implementation in the industrial Python ecosystem. It has widespread production adoption across SCADA and gateway implementations.
2. **Modern API Alignment:** In `pymodbus` v3.x, the legacy `slave` parameter was deprecated and removed in favor of `device_id`, and `ModbusClientMixin` provides clean synchronous methods (`read_coils`, `read_discrete_inputs`, `read_holding_registers`, `read_input_registers`).
3. **No Wire-Protocol Reimplementation Needed:** Writing raw Modbus TCP APUs from scratch is error-prone regarding transaction identifiers, protocol identifiers, length headers, and exception frames. Using `pymodbus` leverages thoroughly tested frame encoding/decoding.
4. **Deterministic In-Process Test Fixture:** To avoid running heavy asynchronous daemons or depending on deprecated `pymodbus.server` internal context modules during test runs, we implemented a lightweight, synchronous, deterministic socket server (`MockModbusServer`) using standard Python `socket` and `struct`. This runs fast (tests finish in <0.5s), runs entirely in-memory, binds to ephemeral ports, and records any unauthorized write attempt.

---

## 3. Adapter Configuration Example

Below is the verified configuration entry added to `config/devices.yaml`:

```yaml
  # ---------------------------------------------------------------------------
  # Device 3: Modbus TCP Controller (Read-Only)
  # Examples: Horner OCS, Schneider M221/M241, WAGO 750, Beckhoff CX
  # Note: Horner controllers require configured Modbus Slave mapping in Cscape.
  # ---------------------------------------------------------------------------
  - id: "modbus_cell_01"
    name: "Assembly Line Modbus Unit"
    enabled: false                     # Set to true when connected to physical network
    adapter_id: "modbus_tcp"
    poll_interval_sec: 1.0
    timeout_sec: 3.0
    connection:
      host: "192.168.1.150"
      port: 502
      unit_id: 1                       # Modbus slave / unit identifier
      timeout: 3.0
      word_order: "big_endian"         # 'big_endian' (ABCD) or 'word_swap' / 'little_endian' (CDAB)
    points:
      # Coils (FC 0x01) - prefix notation
      - point_id: "line_running"
        device_address: "coil:0"       # Wire offset 0 (or '00001')
        data_type: "boolean"
        description: "Main line running status"

      # Discrete Inputs (FC 0x02) - Modicon notation
      - point_id: "estop_ok"
        device_address: "10001"        # Discrete input 1 (wire offset 0)
        data_type: "boolean"
        description: "Emergency Stop circuit healthy"

      # Holding Registers (FC 0x03) - 16-bit analog with engineering scaling
      - point_id: "motor_speed_rpm"
        device_address: "hr:100"       # Holding register offset 100
        data_type: "uint16"
        scale: 0.1                     # Raw 1500 -> 150.0 RPM
        offset: 0.0
        unit: "RPM"
        description: "Main motor operating speed"

      # Holding Registers (FC 0x03) - 32-bit floating point across 2 registers
      - point_id: "oven_temp_celsius"
        device_address: "hr:200"       # Registers 200 + 201
        data_type: "float32"
        unit: "degC"
        description: "Zone 1 temperature reading"

      # Input Registers (FC 0x04) - 16-bit analog sensor
      - point_id: "ambient_pressure_kpa"
        device_address: "ir:10"        # Input register offset 10 (or '30011')
        data_type: "uint16"
        scale: 0.01                    # Raw 10132 -> 101.32 kPa
        unit: "kPa"
        description: "Atmospheric pressure sensor"

      # Bit extraction from a register: bit 5 within holding register 50
      - point_id: "drive_fault_flag"
        device_address: "hr:50.5"
        data_type: "boolean"
        description: "Drive general fault alarm bit"
```

---

## 4. Addressing Conventions: Zero-Based vs. One-Based Mapping Rules

Modbus addressing in industrial applications suffers from ambiguity between protocol wire offsets (0-indexed) and human documentation conventions (1-indexed Modicon tables). To eliminate confusion, the adapter implements strict, explicit rules:

### A. Prefix Notation (0-Based Protocol Offsets)
The prefix notation directly references the Modbus table and uses **0-based wire offsets**:
- `coil:<offset>` &rarr; Read Coils (FC 0x01). `coil:0` reads coil at wire address `0x0000`.
- `di:<offset>` &rarr; Read Discrete Inputs (FC 0x02). `di:0` reads discrete input at wire address `0x0000`.
- `hr:<offset>` &rarr; Read Holding Registers (FC 0x03). `hr:0` reads holding register at wire address `0x0000`.
- `ir:<offset>` &rarr; Read Input Registers (FC 0x04). `ir:0` reads input register at wire address `0x0000`.
- Bit within register: `hr:<offset>.<bit>` or `ir:<offset>.<bit>` (where `<bit>` is `0` to `15`).

### B. Modicon Notation (1-Based Engineering Convention)
The standard 5-digit and 6-digit Modicon numbering maps to 0-based wire offsets as follows:

| Modicon Range (5-digit) | Modicon Range (6-digit) | Modbus Table | Function Code | Offset Calculation |
|:---|:---|:---|:---:|:---|
| `00001` – `09999` | `000001` – `065536` | Coils | FC 0x01 | $\text{Wire Offset} = \text{Address} - 1$ |
| `10001` – `19999` | `100001` – `165536` | Discrete Inputs | FC 0x02 | $\text{Wire Offset} = \text{Address} - 10001$ |
| `30001` – `39999` | `300001` – `365536` | Input Registers | FC 0x04 | $\text{Wire Offset} = \text{Address} - 30001$ |
| `40001` – `49999` | `400001` – `465536` | Holding Registers | FC 0x03 | $\text{Wire Offset} = \text{Address} - 40001$ |

*Bit within register is also supported in Modicon syntax (e.g., `40001.0` to `40001.15`).*

---

## 5. Supported Data Types, Signedness, and Word-Order Behavior

The adapter decodes raw 16-bit register words into normalized data types:

| Data Type | Register Count | Interpretation | Range |
|:---|:---:|:---|:---|
| `BOOLEAN` | 1 bit | Bit 0 of coil/discrete input, or masked bit $((\text{reg} \gg \text{bit}) \ \& \ 1)$ | `0` or `1` |
| `UINT16` | 1 register | Unsigned 16-bit integer (Big-Endian byte order `>H`) | `0` to `65,535` |
| `INT16` | 1 register | Signed two's-complement 16-bit integer (`>h`) | `-32,768` to `32,767` |
| `UINT32` | 2 registers | Unsigned 32-bit integer | `0` to `4,294,967,295` |
| `INT32` | 2 registers | Signed two's-complement 32-bit integer | `-2,147,483,648` to `2,147,483,647` |
| `FLOAT32` | 2 registers | IEEE 754 single-precision float (`>f`) | $\pm 3.4 \times 10^{38}$ |
| `STRING` | $N$ registers | ASCII characters packed 2 chars per register | ASCII text |

### Word Order Handling (32-bit Values)
When reading 32-bit types (`UINT32`, `INT32`, `FLOAT32`), two consecutive 16-bit registers $(W_0, W_1)$ are acquired:
- **`big_endian` (ABCD - Standard Modbus):** $W_0$ is the high word (MSW), $W_1$ is the low word (LSW). Packed as `struct.pack(">HH", W0, W1)`.
- **`little_endian` / `word_swap` (CDAB - Word Swapped):** $W_1$ is the high word, $W_0$ is the low word. Packed as `struct.pack(">HH", W1, W0)`.

### Engineering Scaling and Offset
Scaling is applied through `NormalisedReading.create_good()` in `core/models.py`. The formula is:
$$\text{scaled\_value} = (\text{raw\_value} \times \text{scale}) + \text{offset}$$
Because scaling is executed solely during `NormalisedReading` creation and never inside the adapter decoding logic, double-scaling is prevented.

---

## 6. Read-Only Enforcement Evidence

The adapter implements industrial monitoring-only security controls:

1. **No Write Opcodes:** Modbus function codes 0x05 (Write Single Coil), 0x06 (Write Single Register), 0x0F (Write Multiple Coils), and 0x10 (Write Multiple Registers) are not implemented.
2. **Interface Trap Methods:** Invoking `write()`, `force_coil()`, or `write_register()` raises `ReadOnlySecurityViolation("ModbusTcpAdapter is strictly read-only...")`.
3. **Capability Declaration:** `adapter.metadata.capabilities.is_strictly_read_only` is set to `True`.
4. **Mock Server Audit:** `MockModbusServer` logs every received PDU. Tests verify that across all read operations, connection drop tests, and multi-device polling cycles, `len(server.write_requests_received) == 0`.
5. **Configuration Immutability:** No YAML parameter can enable write capability.

---

## 7. Actual Test Commands and Results

All test runs were executed on the actual codebase.

### Test Run 1: New Modbus Adapter Unit & Integration Tests
```powershell
python -m unittest tests/test_modbus_adapter.py -v
```
**Output:**
```
test_bit_within_register_parsing (tests.test_modbus_adapter.TestModbusAddressParser.test_bit_within_register_parsing) ... ok
test_invalid_address_syntax_rejected (tests.test_modbus_adapter.TestModbusAddressParser.test_invalid_address_syntax_rejected) ... ok
test_modicon_5digit_and_6digit_notation (tests.test_modbus_adapter.TestModbusAddressParser.test_modicon_5digit_and_6digit_notation) ... ok
test_prefix_parsing (tests.test_modbus_adapter.TestModbusAddressParser.test_prefix_parsing) ... ok
test_simulated_and_modbus_devices_coexistence_and_failure_isolation (tests.test_modbus_adapter.TestModbusGatewayIntegration.test_simulated_and_modbus_devices_coexistence_and_failure_isolation) ... ok
test_32bit_float (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_32bit_float) ... ok
test_32bit_integers_and_word_order (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_32bit_integers_and_word_order) ... ok
test_bit_within_register_extraction (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_bit_within_register_extraction) ... ok
test_engineering_scaling (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_engineering_scaling) ... ok
test_protocol_exception_handling (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_protocol_exception_handling) ... ok
test_read_all_four_function_codes (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_read_all_four_function_codes) ... ok
test_read_only_violation_traps (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_read_only_violation_traps) ... ok
test_server_receives_zero_write_requests (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_server_receives_zero_write_requests) ... ok
test_signed_and_unsigned_16bit_registers (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_signed_and_unsigned_16bit_registers) ... ok
test_valid_zero_reading_vs_communication_failure (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_valid_zero_reading_vs_communication_failure) ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.486s

OK
```

### Test Run 2: Full Regression Discovery Test Suite
```powershell
python -m unittest discover -s tests -v
```
**Output:**
```
Ran 48 tests in 0.371s

OK
```
*(48 total tests: 33 Phase 02 core & Mitsubishi tests + 15 Phase 03A Modbus tests, 100% passing).*

### Test Run 3: Legacy Gateway Test Suite
```powershell
python gateway/test_unit.py
```
**Output:**
```
All 7 Python test suites passed successfully!
```

### Test Run 4: Gateway CLI Help Verification
```powershell
python gateway/gateway.py --help
```
**Output:**
```
usage: gateway.py [-h] [--config CONFIG] [--simulate]

Universal PLC Gateway Daemon (STRICTLY READ-ONLY)

options:
  -h, --help       show this help message and exit
  --config CONFIG  Path to devices.yaml or legacy settings.ini (default: auto-detect)
  --simulate       Enable simulation mode for offline verification without physical hardware
```

### Test Run 5: Offline Simulation Smoke Test
```powershell
python -c "from core.config_loader import load_system_config; from core.engine import GatewayEngine; from core.transport_interface import MockTelemetryTransport; cfg = load_system_config(); [setattr(d, 'adapter_id', 'simulated') for d in cfg.devices]; [setattr(d, 'enabled', True) for d in cfg.devices]; tr = MockTelemetryTransport(); eng = GatewayEngine(config=cfg, transport=tr); eng.start(); batches = eng.poll_once(); print('Polled', len(batches), 'batches.'); [print(' - Batch for:', b.device_id, 'connected:', b.connected, 'readings:', len(b.readings)) for b in batches]; eng.stop()"
```
**Output:**
```
Polled 3 batches.
 - Batch for: melsec_q03 connected: True readings: 16
 - Batch for: sim_cell_02 connected: True readings: 2
 - Batch for: modbus_cell_01 connected: True readings: 6
```

---

## 8. Known Limitations and Hardware Validation Required

1. **Horner OCS Controller Specifics:**
   - Horner controllers program in Cscape. They do **not** automatically expose all internal `%R` or `%M` registers to Modbus TCP unless configured as a Modbus Slave in Cscape Hardware Configuration or Protocol configuration.
   - Deploying to a Horner unit requires verifying the specific register mapping in the controller's logic program.
2. **Contiguous Range Read Optimization:**
   - The current adapter reads points individually using discrete Modbus requests.
   - For points spanning close addresses (e.g. `hr:0` to `hr:50`), coalescing contiguous registers into a single FC 0x03 read of 51 registers will optimize high-throughput polling.
3. **Physical Hardware Validation Needed:**
   - Validation against physical hardware in the automation lab:
     - Schneider M221 / M241 PLC over RJ-45 Ethernet.
     - Horner XL4 / X2 OCS over physical network.
     - WAGO 750 or Beckhoff CX controller.
     - Verifying scan time under 100ms on a 100 Mbps industrial switch.

---

## 9. Exact Windows Commands to Run Tests and Simulation

To reproduce verification in Windows PowerShell:

```powershell
# 1. Activate virtual environment
.\gateway\.venv\Scripts\Activate.ps1

# 2. Run new Modbus adapter tests
python -m unittest tests/test_modbus_adapter.py -v

# 3. Run all unit and integration tests (48 tests)
python -m unittest discover -s tests -v

# 4. Run legacy gateway test suite (7 suites)
python gateway/test_unit.py

# 5. Check gateway daemon help
python gateway/gateway.py --help

# 6. Execute offline multi-device simulation
python -c "from core.config_loader import load_system_config; from core.engine import GatewayEngine; from core.transport_interface import MockTelemetryTransport; cfg = load_system_config(); [setattr(d, 'adapter_id', 'simulated') for d in cfg.devices]; [setattr(d, 'enabled', True) for d in cfg.devices]; tr = MockTelemetryTransport(); eng = GatewayEngine(config=cfg, transport=tr); eng.start(); batches = eng.poll_once(); print('Polled', len(batches), 'batches.'); [print(' - Batch for:', b.device_id, 'connected:', b.connected, 'readings:', len(b.readings)) for b in batches]; eng.stop()"
```

---

## 10. Recommended Next Phase

### Phase 03B: Multi-Register Range Optimization & OPC UA / Secondary Adapters
1. **Modbus Range Coalescing:** Group contiguous register reads into batch requests to minimize network transactions.
2. **OPC UA Read-Only Adapter:** Implement `adapters/opcua/opcua_client.py` using `asyncua` for unified tag monitoring.
3. **Telemetry Exporters:** Add read-only MQTT / Kafka telemetry export alongside USB Serial.

---

## Conclusion

Phase 03A is complete:
- **Implemented:** Modbus TCP read-only protocol adapter, address parser, 16/32-bit decoder, in-process mock server, and 15 automated integration tests.
- **Enforced:** Zero write opcodes, write method trap exceptions, and verified zero write network frames.
- **Preserved:** 100% backward compatibility with Phase 02 universal architecture and legacy Mitsubishi Q-series test suites.
