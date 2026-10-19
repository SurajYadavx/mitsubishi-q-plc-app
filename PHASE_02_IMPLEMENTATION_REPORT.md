# PHASE 02 IMPLEMENTATION REPORT — UNIVERSAL PLC CORE

**Document Version:** 1.0.0  
**Project:** Universal PLC Monitoring Platform  
**Phase:** 02 — Implement the Universal PLC Core  
**Author:** Senior Industrial Automation Software Architect & OT Cybersecurity Specialist  
**Date:** October 19, 2026  
**Status:** Successfully Implemented & Empirically Verified (100% Test Pass Rate)

---

## 1. Executive Summary

Phase 02 transitions the repository from a single-target monitor coupled to the Mitsubishi Q03UDECPU into an extensible, protocol-independent **Universal PLC Platform Core**. 

The core implementation decouples protocol drivers, data models, device registries, and transports into dedicated layers while preserving 100% backward compatibility with the existing hardware setup (Windows gateway &rarr; USB serial COM9 &rarr; ESP8266 NodeMCU &rarr; EMQX Cloud &rarr; GitHub Pages HMI).

### Key Accomplishments
1. **Abstract Read-Only Interface (`core/adapter_interface.py`):** Established `IPlcProtocolAdapter` defining protocol capabilities, connection lifecycle, and batch reading without write/force methods.
2. **Normalized Industrial Data Models (`core/models.py`):** Implemented `PointDefinition`, `NormalisedReading`, `NormalisedTelemetryBatch`, `DataQuality`, and `PointDataType`. Distinguishes valid zeros from sensor/socket errors.
3. **Decoupled Mitsubishi MC Protocol Adapter (`adapters/mitsubishi/mc_protocol.py`):** Extracted Q-series 3E binary protocol logic into a standalone adapter. Refactored `gateway/src/plc_reader.py` to delegate directly to this adapter, eliminating duplicated code while maintaining legacy import compatibility.
4. **Multi-Device Configuration Registry (`config/devices.yaml` & `core/config_loader.py`):** Created a validated YAML configuration registry with strict schema checks, address validation, duplicate ID rejection, and automatic fallback to legacy `gateway/config/settings.ini`.
5. **Decoupled Telemetry Transports (`core/transport_interface.py`):** Created `ITelemetryTransport` and `SerialTelemetryTransport`, isolating data acquisition from serial streaming and cloud messaging.
6. **Multi-Device Gateway Engine (`core/engine.py` & `gateway/gateway.py`):** Refactored the gateway daemon into a multi-device coordinator with isolated device failure handling and offline simulation support (`--simulate`).
7. **Comprehensive Automated Test Harness (`tests/`):** Implemented 33 new unit and negative security tests with a 100% pass rate. Confirmed the existing 7 legacy Python test suites continue to pass with zero regressions.

---

## 2. Files Created and Modified

### A. Files Created
| File Path | Purpose |
|---|---|
| `core/__init__.py` | Core package initialization exporting key models. |
| `core/models.py` | Normalized industrial data models (`PointDefinition`, `NormalisedReading`, `NormalisedTelemetryBatch`, `DataQuality`, `PointDataType`). |
| `core/adapter_interface.py` | Abstract `IPlcProtocolAdapter`, `ProtocolMetadata`, `AdapterCapabilities`, and `ReadOnlySecurityViolation`. |
| `core/simulated_adapter.py` | Deterministic `SimulatedPlcAdapter` for offline execution and automated test suites. |
| `core/adapter_registry.py` | Central `AdapterRegistry` factory mapping adapter IDs to driver implementations. |
| `core/config_loader.py` | Multi-device configuration loader with validation rules and legacy INI fallback. |
| `core/transport_interface.py` | Decoupled transport abstractions (`ITelemetryTransport`, `SerialTelemetryTransport`, `MockTelemetryTransport`). |
| `core/engine.py` | Multi-device `GatewayEngine` coordinating polling lifecycles and error isolation. |
| `adapters/mitsubishi/__init__.py` | Mitsubishi adapters package initialization. |
| `adapters/mitsubishi/mc_protocol.py` | Canonical `MitsubishiMcProtocolAdapter` implementing `IPlcProtocolAdapter`. |
| `config/devices.yaml` | Multi-device YAML configuration defining Q03UDECPU and simulated cell. |
| `tests/__init__.py` | Test package initialization. |
| `tests/test_core_models.py` | Unit tests for data quality, scaling, and legacy serialization roundtrips. |
| `tests/test_config_loader.py` | Unit tests for schema validation, duplicate ID rejection, and legacy INI fallback. |
| `tests/test_mc_adapter.py` | Unit tests for Q-series hex address conversions and disconnected failure quality. |
| `tests/test_read_only_enforcement.py` | Automated negative security tests proving write/force operations are rejected. |
| `tests/test_gateway_core.py` | Integration tests for multi-device polling, failure isolation, and monotonic sequences. |
| `PHASE_02_IMPLEMENTATION_REPORT.md` | This deliverable report. |

### B. Files Modified
| File Path | Description of Changes |
|---|---|
| `gateway/src/plc_reader.py` | Refactored to delegate to `adapters/mitsubishi/mc_protocol.py`. Preserves legacy class `PLCReader`, helper functions, and `DeviceReading` dataclass for 100% backward compatibility. |
| `gateway/gateway.py` | Refactored to load configuration via `core.config_loader` and coordinate polling via `core.engine.GatewayEngine`. Preserves CLI options, logging handlers, and graceful shutdown. |

---

## 3. Architecture and Dependency Changes

### A. Decoupled Multi-Tier Architecture

```
[ Field Layer: Multi-Vendor PLCs ]
  • Mitsubishi Q03UDECPU (MC 3E)     • Simulated PLC (Deterministic)
  • [Future] Modbus TCP              • [Future] OPC UA
          │
          ▼
[ Adapter Layer: IPlcProtocolAdapter ]
  • adapters/mitsubishi/mc_protocol.py
  • core/simulated_adapter.py
          │
          ▼
[ Core Platform: GatewayEngine & Registry ]
  • core/engine.py (Multi-Device Polling & Fault Isolation)
  • core/models.py (NormalisedTelemetryBatch, DataQuality)
  • core/config_loader.py (config/devices.yaml with INI fallback)
          │
          ▼
[ Transport Layer: ITelemetryTransport ]
  • SerialTelemetryTransport (COM9 @ 115200 baud -> NodeMCU ESP8266)
  • MockTelemetryTransport (In-memory testing)
  • [Future Phase 03] Native MQTT TLS Transport
          │
          ▼
[ Edge Microcontroller & Cloud HMI ]
  • NodeMCU ESP8266 (plc_monitor.ino)
  • EMQX Cloud Serverless (MQTTS Port 8883 / WSS Port 8084)
  • Static GitHub Pages HMI (web/index.html & app.js)
```

### B. Python Dependency Changes
- **Added:** `pyyaml>=6.0.3` (Installed into environment and `gateway/.venv` for YAML device registry parsing).
- **Preserved:** `pymcprotocol==0.3.0` and `pyserial==3.5` continue to be used without version changes.

---

## 4. Old-to-New Migration Details

To avoid maintaining two competing codebases, the existing components were cleanly migrated:

1. **`PLCReader` Delegation:**
   - Previous state: `gateway/src/plc_reader.py` contained hardcoded socket handling and address translation.
   - New state: All protocol mechanics reside in `adapters/mitsubishi/mc_protocol.py`. `PLCReader` in `gateway/src/plc_reader.py` now instantiates `MitsubishiMcProtocolAdapter` and translates `NormalisedTelemetryBatch` to `DeviceReading`. Any external script calling `from plc_reader import PLCReader` continues to function without change.
2. **Configuration Migration:**
   - Previous state: `gateway/config/settings.ini` hardcoded a single PLC IP and static point lists.
   - New state: `config/devices.yaml` enables arbitrary multi-device definitions with engineering unit scaling. If `devices.yaml` is absent or `--config path/to/settings.ini` is specified, `load_system_config()` automatically converts the legacy INI into a valid `GatewayConfig` at runtime.
3. **Serial JSON Schema Compatibility:**
   - Previous state: `SerialPublisher` generated `{ "type": "plc_reading", "gw_id": "...", "seq": ..., "ts": "...", "connected": true, "inputs": { "X0":0,... }, "outputs": { "Y20":1,... } }`.
   - New state: `NormalisedTelemetryBatch.to_legacy_payload()` dynamically transforms normalized point readings into the exact `inputs` and `outputs` dictionaries expected by `plc_monitor.ino` and `app.js`.

---

## 5. Actual Test Commands and Empirical Results

### A. Preflight Baseline Test Suite
```powershell
python gateway/test_unit.py
```
**Output:**
```text
All 7 Python test suites passed successfully!
```
*Result: 7/7 suites passed.*

### B. Phase 02 Comprehensive Test Suite
```powershell
python -m unittest discover -s tests -v
```
**Output:**
```text
test_duplicate_device_id_rejection (test_config_loader.TestConfigLoader) ... ok
test_duplicate_point_id_rejection (test_config_loader.TestConfigLoader) ... ok
test_legacy_ini_migration_fallback (test_config_loader.TestConfigLoader) ... ok
test_load_default_devices_yaml (test_config_loader.TestConfigLoader) ... ok
test_load_system_config_auto_detection (test_config_loader.TestConfigLoader) ... ok
test_malformed_address_rejection (test_config_loader.TestConfigLoader) ... ok
test_unknown_adapter_rejection (test_config_loader.TestConfigLoader) ... ok
test_engineering_scaling (test_core_models.TestNormalisedReading) ... ok
test_failure_reading_has_null_and_error (test_core_models.TestNormalisedReading) ... ok
test_timestamp_utc_timezone_awareness (test_core_models.TestNormalisedReading) ... ok
test_valid_zero_reading (test_core_models.TestNormalisedReading) ... ok
test_batch_serialization_roundtrip (test_core_models.TestNormalisedTelemetryBatch) ... ok
test_to_legacy_payload_compatibility (test_core_models.TestNormalisedTelemetryBatch) ... ok
test_invalid_point_rejection (test_core_models.TestPointDefinition) ... ok
test_serialization_roundtrip (test_core_models.TestPointDefinition) ... ok
test_valid_point_creation (test_core_models.TestPointDefinition) ... ok
test_engine_initializes_enabled_devices (test_gateway_core.TestGatewayCoreEngine) ... ok
test_independent_device_failure_isolation (test_gateway_core.TestGatewayCoreEngine) ... ok
test_monotonic_sequence_numbering (test_gateway_core.TestGatewayCoreEngine) ... ok
test_shutdown_lifecycle (test_gateway_core.TestGatewayCoreEngine) ... ok
test_decimal_devices_conversion (test_mc_adapter.TestMitsubishiMcAdapter) ... ok
test_diagnostics_and_device_info (test_mc_adapter.TestMitsubishiMcAdapter) ... ok
test_disconnected_poll_returns_comm_failure_not_zeros (test_mc_adapter.TestMitsubishiMcAdapter) ... ok
test_implements_interface (test_mc_adapter.TestMitsubishiMcAdapter) ... ok
test_invalid_address_raises_value_error (test_mc_adapter.TestMitsubishiMcAdapter) ... ok
test_q_series_hex_address_conversion (test_mc_adapter.TestMitsubishiMcAdapter) ... ok
test_ast_inspection_no_write_opcodes_in_mc_adapter (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok
test_capability_flags_affirm_strictly_read_only (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok
test_mc_adapter_force_coil_raises_security_violation (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok
test_mc_adapter_write_raises_security_violation (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok
test_mc_adapter_write_register_raises_security_violation (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok
test_sim_adapter_force_coil_raises_security_violation (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok
test_sim_adapter_write_raises_security_violation (test_read_only_enforcement.TestReadOnlyEnforcement) ... ok

----------------------------------------------------------------------
Ran 33 tests in 0.148s

OK
```
*Result: 33/33 tests passed (100% pass rate).*

### C. Post-Refactoring Regression Check
```powershell
python gateway/test_unit.py
```
**Output:**
```text
All 7 Python test suites passed successfully!
```
*Result: 7/7 suites passed.*

---

## 6. Read-Only Enforcement Evidence

1. **Interface Segregation:** The `IPlcProtocolAdapter` abstract base class explicitly defines security violation traps for write operations:
   ```python
   def write(self, *args, **kwargs):
       raise ReadOnlySecurityViolation("Write operations are strictly prohibited on read-only adapter.")
   ```
2. **Static AST Analysis:** Automated test `test_ast_inspection_no_write_opcodes_in_mc_adapter` inspects the source text of `adapters/mitsubishi/mc_protocol.py` and asserts that write opcodes (`0x1401`, `0x1402`, `0x1601`, `batchwrite`, `randomwrite`) are completely absent.
3. **Data Quality Integrity:** When a PLC socket is disconnected or encounters read errors, `NormalisedReading.create_failure()` sets `raw_value=None` and `quality=DataQuality.COMM_FAILURE`. Tests prove that a communication failure never fabricates a false `0` or `False` state.

---

## 7. Backward-Compatibility Evidence

1. **Legacy Test Verification:** `gateway/test_unit.py` verifies `_device_to_addr`, `_addr_to_label`, `DeviceReading`, and `SerialPublisher` formatting. All 7 suites passed with zero modifications to the test file.
2. **Legacy Payload Contract:** Automated test `test_to_legacy_payload_compatibility` verifies that `NormalisedTelemetryBatch.to_legacy_payload()` produces identical JSON frames for `inputs` (`X0–X7`) and `outputs` (`Y20–Y27`).
3. **Configuration Fallback:** `load_legacy_ini_config()` successfully reads `gateway/config/settings.ini` when `devices.yaml` is not present, producing an identical internal `GatewayConfig`.

---

## 8. Features Deferred and Justification

1. **Direct Native MQTT Transport (`paho-mqtt`):**
   - *Status:* Deferred to Phase 03.
   - *Reason:* `paho-mqtt` is not currently installed in the baseline environment. Attempting an unverified cloud MQTT implementation during this foundational phase would expand scope unnecessarily. The `ITelemetryTransport` interface was designed and tested to easily accept an `MqttTelemetryTransport` implementation in Phase 03.
2. **Dynamic UI Generation in the Frontend:**
   - *Status:* Deferred to Phase 04.
   - *Reason:* Modifying `web/index.html` or `app.js` in Phase 02 would risk breaking the live GitHub Pages supervisory monitor prior to field testing.

---

## 9. Known Limitations

1. **Physical Q03UDECPU Testing:** Verification was conducted using deterministic offline mocks and unit tests; live socket communication on port 5010 remains pending physical laboratory access.
2. **Hardware Serial Buffering:** Live testing of `SerialTelemetryTransport` on `COM9` requires the physical NodeMCU ESP8266 board plugged into USB.
3. **Multi-Drop Bus Topology:** The current engine polls devices sequentially; highly parallel async polling across dozens of devices is planned for future engine expansion.

---

## 10. Exact Commands for Running the Refactored Gateway on Windows

### A. Run in Simulated Demonstration Mode (Offline / No Hardware Required)
```powershell
# From the repository root:
python gateway/gateway.py --simulate
```
*Logs normal deterministic telemetry batches without needing a physical PLC or serial port.*

### B. Run in Production Mode with Multi-Device YAML Config
```powershell
# From the repository root:
python gateway/gateway.py --config config/devices.yaml
```

### C. Run with Legacy INI Configuration Fallback
```powershell
# From the repository root:
python gateway/gateway.py --config gateway/config/settings.ini
```

### D. Execute All Automated Tests
```powershell
# Run the 33 Phase 02 core unit and security tests:
python -m unittest discover -s tests -v

# Run the 7 legacy gateway test suites:
python gateway/test_unit.py
```

---

## 11. Recommended Exact Scope for Phase 03

**Phase 03: Protocol Adapter Expansion & Native MQTT Transport**

1. **Implement Modbus TCP Adapter (`adapters/modbus/modbus_tcp.py`):**
   - Conforming to `IPlcProtocolAdapter`.
   - Supports reading Coils (FC 0x01), Discrete Inputs (FC 0x02), and Holding Registers (FC 0x03).
   - Read-only enforcement: rejects FC 0x05, 0x06, 0x0F, 0x10.
2. **Implement Native MQTT Telemetry Transport (`core/transports/mqtt_transport.py`):**
   - Conforming to `ITelemetryTransport`.
   - Uses `paho-mqtt` with TLS 1.2/1.3 and certificate verification.
   - Publishes normalized telemetry to `plcmon/{gateway_id}/{device_id}/telemetry`.
3. **Implement Protocol Simulation Test Servers:**
   - Standalone mock Modbus TCP server for automated integration tests.
4. **Expand Device Registry:**
   - Update `config/devices.yaml` to include Modbus TCP controller profiles (e.g. Horner OCS slave mapping).

---

*End of Phase 02 Implementation Report.*
