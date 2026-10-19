# PHASE 03B — INTEGRATION, ADDRESSING, DEPENDENCY, AND READ-ONLY SECURITY AUDIT REPORT

**Universal PLC Monitoring Platform Migration**  
**Document Status:** Complete & Verified  
**Date:** 2026-10-19  
**Auditor:** Senior Industrial Automation Software Architect & Protocol Engineer  
**Scope:** Phase 03B Comprehensive Audit (Integration, Addressing, Dependency, and Read-Only Security Invariants)

---

## Executive Summary

Phase 03B conducted an exhaustive, empirical code and runtime audit of the newly integrated **Modbus TCP Read-Only Adapter** (`adapters/modbus/modbus_tcp.py`), the shared adapter interface (`core/adapter_interface.py`), configuration validation (`core/config_loader.py`), dependency integrity, and multi-device gateway orchestration (`core/engine.py`).

All ten specific audit areas achieved a verified **PASS**. The adapter strictly enforces industrial read-only monitoring, cleanly handles protocol boundaries, traps all prohibited write operations, isolates multi-vendor polling failures, and maintains 100% backward compatibility with existing Mitsubishi Q-series monitoring and legacy test suites.

---

## Audit Matrix & Results Summary

| # | Audit Area | Status | Evidence / Verification Method |
|:---:|:---|:---:|:---|
| **1** | Zero-based Addressing (Prefix Notation) | **PASS** | `test_prefix_parsing` validates `coil:`, `di:`, `hr:`, `ir:` mapping to wire offsets 0–65535 and corresponding memory tables. |
| **2** | Modicon Reference-Number Conversion & Boundaries | **PASS** | `test_modicon_boundaries_and_invalid_references` validates 5/6-digit limits (`00001`–`09999`, `000001`–`065536`, `10001`–`19999`, `30001`–`39999`, `40001`–`49999`) and rejects invalid/out-of-bound references (`00000`, `065537`, `20001`, `50001`, `99999`). |
| **3** | Bit-Within-Register Numbering & Restrictions | **PASS** | `test_bit_within_register_extraction` and `test_bit_indexing_restrictions_and_bounds` verify bits 0 (LSB) to 15 (MSB); bit indexing on discrete 1-bit points (`coil:0.0`, `10001.1`) is strictly rejected. |
| **4** | Data Type Decoding, Word Order & Incomplete Reads | **PASS** | `test_signed_and_unsigned_16bit_registers`, `test_32bit_integers_and_word_order`, `test_32bit_float`, and `test_decode_registers_incomplete_reads_for_32bit` verify INT16, UINT16, INT32, UINT32, FLOAT32, word swapping, and fast failure on incomplete reads (< 2 registers). |
| **5** | Scaling & Offset Applied Exactly Once | **PASS** | `test_engineering_scaling` proves raw registers remain unscaled in the adapter and are transformed exactly once in `NormalisedReading.create_good()`. |
| **6** | Read-Only Network Traffic (FC 0x01–0x04 Only) | **PASS** | `test_server_receives_zero_write_requests` verifies `server.write_requests_received == 0`. Static AST audit `test_ast_static_inspection_no_write_calls_in_adapter` proves zero write API calls in adapter source. |
| **7** | Write-Method Calls Fail Closed | **PASS** | `test_read_only_violation_traps` verifies calling `write()`, `force_coil()`, `write_register()`, `write_coils()`, or `write_registers()` raises `ReadOnlySecurityViolation`. |
| **8** | Timeouts, Socket Recycling & Device Failure Isolation | **PASS** | `test_protocol_exception_handling`, `test_adapter_reconnect_lifecycle`, and `test_simulated_and_modbus_devices_coexistence_and_failure_isolation` verify graceful exception handling, socket recycling, and independent multi-device execution. |
| **9** | Dependency Compatibility in Virtual Environment | **PASS** | `gateway/.venv` verified with `pip check` (0 broken requirements), `pymodbus==3.16.1`, `pymcprotocol==0.3.0`, `pyserial==3.5`, `pyyaml==6.0.3`. |
| **10** | Continued Compatibility with Mitsubishi & Legacy Systems | **PASS** | 56/56 unified tests passing (0.47s); 7/7 legacy test suites in `gateway/test_unit.py` passing; `gateway.py --help` and `--simulate` verified. |

---

## Detailed Findings and Empirical Evidence

### Area 1: Zero-Based Addressing
- **Implementation:** Explicit prefix parsing in `parse_modbus_address()` supports `coil:` / `c:`, `di:` / `disc:`, `hr:` / `holding:`, and `ir:` / `input:`.
- **Wire Mapping:** Directly maps the numeric string to 0-based protocol offsets `0` to `65535`.
- **Negative Cases:** Negative offsets (`hr:-5`), non-numeric strings (`hr:abc`), empty strings, and out-of-range offsets (`hr:70000`) raise `ValueError`.
- **Verdict:** **PASS**

### Area 2: Modicon Reference-Number Conversion & Boundaries
- **5-Digit & 6-Digit Mapping Rules:**
  - `00001` &rarr; Coil offset 0; `09999` &rarr; Coil offset 9998; `065536` &rarr; Coil offset 65535.
  - `10001` &rarr; Discrete Input offset 0; `19999` &rarr; Discrete Input offset 9998; `165536` &rarr; Discrete Input offset 65535.
  - `30001` &rarr; Input Register offset 0; `39999` &rarr; Input Register offset 9998; `365536` &rarr; Input Register offset 65535.
  - `40001` &rarr; Holding Register offset 0; `49999` &rarr; Holding Register offset 9998; `465536` &rarr; Holding Register offset 65535.
- **Boundary & Invalid Handling:**
  - Offsets calculated as $< 0$ (e.g., `00000`, `10000`, `30000`, `40000`) raise `ValueError`.
  - References exceeding maximum 16-bit register limits (e.g., `065537`, `165537`, `365537`, `465537`) raise `ValueError`.
  - Non-standard Modicon tables starting with `2`, `5`, or `9` (e.g., `20001`, `50001`, `99999`) raise `ValueError(f"Invalid Modicon reference... Standard Modicon references must begin with 0, 1, 3, or 4")`.
- **Verdict:** **PASS**

### Area 3: Bit-Within-Register Behavior
- **Bit Indexing Convention:**
  - Documented as 0 to 15, representing standard IEC 61131-3 LSB-to-MSB numbering.
  - Bit 0 extracts $((\text{reg} \gg 0) \ \& \ 1)$ (mask `0x0001`, value 1).
  - Bit 15 extracts $((\text{reg} \gg 15) \ \& \ 1)$ (mask `0x8000`, value 32768).
- **Security & Syntax Restrictions:**
  - Bit indexing on discrete 1-bit points (`coil:0.0`, `00001.1`, `di:0.5`, `10001.2`) is explicitly rejected with `ValueError("Bit indexing is not applicable to 1-bit discrete point...")`.
  - Invalid bit ranges (`hr:0.16`, `hr:0.-1`, `hr:0.abc`) raise `ValueError`.
- **Verdict:** **PASS**

### Area 4: Register Decoding, Word Order & Incomplete Reads
- **Decoding Coverage:**
  - `INT16`: Verified negative values (`-1` as `0xFFFF`, `-32768` as `0x8000`).
  - `UINT16`: Verified range up to `65535` (`50000` as `0xC350`).
  - `UINT32`: Verified `305419896` (`0x12345678`) across registers `[0x1234, 0x5678]`.
  - `INT32`: Verified negative `-100000` (`0xFFFE7960`) across registers `[0xFFFE, 0x7960]`.
  - `FLOAT32`: Verified IEEE 754 float `123.456` across registers `[0x42F6, 0xE979]`.
  - `BOOLEAN`: Normalized to strictly return `0` or `1`.
- **Word Order Handling:**
  - `big_endian` (ABCD): MSW in $W_0$, LSW in $W_1$.
  - `little_endian` / `word_swap` (CDAB): LSW in $W_0$, MSW in $W_1$.
  - Unrecognized word order strings raise `ValueError`.
- **Incomplete Reads:**
  - If a 32-bit type receives only 1 register, `_decode_registers` raises `ValueError("Incomplete read: decoding ... requires 2 consecutive registers, got 1")`.
  - Empty register lists raise `ValueError("No register data provided...")`.
- **Verdict:** **PASS**

### Area 5: Scaling and Offset Application
- **Single Evaluation Verification:**
  - Adapter `_read_single_point()` and `_decode_registers()` return purely raw numerical values without applying engineering multipliers.
  - Normalization occurs in `NormalisedReading.create_good(point, raw_val)`:
    $$\text{scaled\_value} = (\text{float}(1500) \times 0.1) + (-5.0) = 145.0$$
  - `NormalisedReading.raw_value` retains `1500` and `NormalisedReading.scaled_value` retains `145.0`.
  - No double-scaling can occur because the adapter has no access to the point's scale parameters during decode.
- **Verdict:** **PASS**

### Area 6: Read-Only Network Traffic
- **Protocol Verification:**
  - `ModbusTcpAdapter` implements only:
    - FC 0x01: Read Coils (`read_coils`)
    - FC 0x02: Read Discrete Inputs (`read_discrete_inputs`)
    - FC 0x03: Read Holding Registers (`read_holding_registers`)
    - FC 0x04: Read Input Registers (`read_input_registers`)
  - Write function codes 0x05, 0x06, 0x0F, and 0x10 are completely absent.
- **Mock Server Logging:**
  - `MockModbusServer` intercepts and logs all incoming function codes.
  - During test execution, `len(server.write_requests_received) == 0`.
- **Static AST Verification:**
  - `test_ast_static_inspection_no_write_calls_in_adapter` parses `adapters/modbus/modbus_tcp.py` and asserts zero invocations of `write_coil`, `write_coils`, `write_register`, `write_registers`, `mask_write_register`, or `read_write_multiple_registers`.
- **Verdict:** **PASS**

### Area 7: Write-Method Fail-Closed Traps
- **Method Security Enforcement:**
  - Calling any write method on `ModbusTcpAdapter` fails closed:
    - `adapter.write(...)` &rarr; raises `ReadOnlySecurityViolation`
    - `adapter.force_coil(...)` &rarr; raises `ReadOnlySecurityViolation`
    - `adapter.write_register(...)` &rarr; raises `ReadOnlySecurityViolation`
    - `adapter.write_coils(...)` &rarr; raises `ReadOnlySecurityViolation`
    - `adapter.write_registers(...)` &rarr; raises `ReadOnlySecurityViolation`
- **Configuration Security:**
  - Configuration loader `core/config_loader.py` contains no parameters for enabling write permissions or output forcing.
- **Verdict:** **PASS**

### Area 8: Lifecycle, Timeouts, Socket Cleanup & Failure Isolation
- **Socket Lifecycle & Cleanup:**
  - `connect()` safely closes and recycles any existing `_client` before allocating a new socket.
  - `disconnect()` calls `_client.close()`, resets `_connected = False`, and cleans up references.
  - `test_adapter_reconnect_lifecycle` proves that cycling `connect() &rarr; disconnect() &rarr; connect()` restores communication deterministically.
- **Protocol Exceptions & Failure Isolation:**
  - Modbus protocol exceptions (e.g., Code 0x02 Illegal Data Address) are caught per point, returning `DataQuality.BAD` without dropping the TCP connection.
  - Network-level drops (e.g. server socket closed) set `self._connected = False` and populate the batch with `connected=False` and `DataQuality.COMM_FAILURE`.
  - Multi-device integration test confirms that when the Modbus device fails, the Simulated PLC continues polling at 20 Hz without interruption.
- **Verdict:** **PASS**

### Area 9: Pinned Dependency & Environment Verification
- **Environment Status:**
  - Checked virtual environment `.\gateway\.venv`:
    - `pip check` &rarr; `No broken requirements found.`
    - `pymodbus` &rarr; `3.16.1`
    - `pymcprotocol` &rarr; `0.3.0`
    - `pyserial` &rarr; `3.5`
    - `PyYAML` &rarr; `6.0.3`
  - Dependencies pinned in `gateway/requirements.txt`:
    ```
    pymcprotocol>=0.3.0
    pyserial>=3.5
    pymodbus>=3.16.0,<4.0.0
    pyyaml>=6.0.1
    ```
- **API Compatibility:**
  - Verified that `pymodbus.client.ModbusTcpClient` operates with modern keyword arguments (`device_id` rather than deprecated/removed `slave`), avoiding deprecation warnings and runtime TypeErrors.
- **Verdict:** **PASS**

### Area 10: Continued Backward Compatibility
- **Mitsubishi Q-Series Compatibility:**
  - `test_mc_adapter.py` passes all 6 tests (MC 3E frame generation, hex addressing X/Y/B/W, decimal D/M/R, diagnostics, read-only AST checks).
- **Legacy Gateway Compatibility:**
  - `python gateway/test_unit.py` passes all 7 legacy test suites with zero failures.
- **Simulated Adapter:**
  - `SimulatedPlcAdapter` verified in standalone tests, gateway engine isolation tests, and CLI `--simulate` mode.
- **Daemon CLI Smoke Check:**
  - `python gateway/gateway.py --help` exits cleanly with code 0.
- **Verdict:** **PASS**

---

## Actual Test Execution Commands and Outputs

### Command 1: Extended Modbus Adapter Test Suite (23 Tests)
```powershell
.\gateway\.venv\Scripts\python.exe -m unittest tests/test_modbus_adapter.py -v
```
**Output:**
```
test_bit_indexing_restrictions_and_bounds (tests.test_modbus_adapter.TestModbusAddressParser.test_bit_indexing_restrictions_and_bounds) ... ok
test_bit_within_register_parsing (tests.test_modbus_adapter.TestModbusAddressParser.test_bit_within_register_parsing) ... ok
test_invalid_address_syntax_rejected (tests.test_modbus_adapter.TestModbusAddressParser.test_invalid_address_syntax_rejected) ... ok
test_modicon_5digit_and_6digit_notation (tests.test_modbus_adapter.TestModbusAddressParser.test_modicon_5digit_and_6digit_notation) ... ok
test_modicon_boundaries_and_invalid_references (tests.test_modbus_adapter.TestModbusAddressParser.test_modicon_boundaries_and_invalid_references) ... ok
test_prefix_parsing (tests.test_modbus_adapter.TestModbusAddressParser.test_prefix_parsing) ... ok
test_simulated_and_modbus_devices_coexistence_and_failure_isolation (tests.test_modbus_adapter.TestModbusGatewayIntegration.test_simulated_and_modbus_devices_coexistence_and_failure_isolation) ... ok
test_32bit_float (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_32bit_float) ... ok
test_32bit_integers_and_word_order (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_32bit_integers_and_word_order) ... ok
test_adapter_reconnect_lifecycle (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_adapter_reconnect_lifecycle) ... ok
test_ast_static_inspection_no_write_calls_in_adapter (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_ast_static_inspection_no_write_calls_in_adapter) ... ok
test_bit_within_register_extraction (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_bit_within_register_extraction) ... ok
test_engineering_scaling (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_engineering_scaling) ... ok
test_protocol_exception_handling (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_protocol_exception_handling) ... ok
test_read_all_four_function_codes (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_read_all_four_function_codes) ... ok
test_read_only_violation_traps (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_read_only_violation_traps) ... ok
test_server_receives_zero_write_requests (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_server_receives_zero_write_requests) ... ok
test_signed_and_unsigned_16bit_registers (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_signed_and_unsigned_16bit_registers) ... ok
test_valid_zero_reading_vs_communication_failure (tests.test_modbus_adapter.TestModbusTcpAdapterIntegration.test_valid_zero_reading_vs_communication_failure) ... ok
test_boolean_decoding_normalization (tests.test_modbus_adapter.TestRegisterDecodingAndEdgeCases.test_boolean_decoding_normalization) ... ok
test_decode_registers_empty_list (tests.test_modbus_adapter.TestRegisterDecodingAndEdgeCases.test_decode_registers_empty_list) ... ok
test_decode_registers_incomplete_reads_for_32bit (tests.test_modbus_adapter.TestRegisterDecodingAndEdgeCases.test_decode_registers_incomplete_reads_for_32bit) ... ok
test_decode_registers_unsupported_word_order (tests.test_modbus_adapter.TestRegisterDecodingAndEdgeCases.test_decode_registers_unsupported_word_order) ... ok

----------------------------------------------------------------------
Ran 23 tests in 0.702s

OK
```

### Command 2: Complete Project Test Discovery Suite (56 Tests)
```powershell
.\gateway\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
**Output:**
```
Ran 56 tests in 0.467s

OK
```

### Command 3: Legacy Test Suite
```powershell
.\gateway\.venv\Scripts\python.exe gateway/test_unit.py
```
**Output:**
```
All 7 Python test suites passed successfully!
```

### Command 4: Virtual Environment Dependency Check
```powershell
.\gateway\.venv\Scripts\pip.exe check
```
**Output:**
```
No broken requirements found.
```

---

## Remaining Hardware Limitations & Constraints

1. **Lab Physical Validation:**
   - Automated testing relies on the in-process `MockModbusServer` fixture.
   - Physical hardware testing against Schneider Electric M221/M241 and Horner XL4 OCS over physical Ethernet remains required before operational deployment.
2. **Horner Cscape Register Mapping:**
   - Horner controllers require explicit Modbus slave mapping in the user's Cscape project. The adapter cannot discover or read unmapped internal variables.
3. **Multi-Register Range Coalescing:**
   - Current reads are executed per configured point. Batch range coalescing across adjacent addresses (e.g. reading 50 contiguous registers in a single transaction) is scheduled for future performance optimization.

---

## Architectural Recommendation

The Universal PLC Monitoring Platform core, the Mitsubishi MC Protocol adapter, and the Modbus TCP adapter are fully decoupled, strictly read-only, thoroughly covered by automated regression tests, and architecturally verified.

**Recommendation:**  
The platform is **READY** to proceed to the **Windows Local Agent and Device Discovery Engine** phase.
