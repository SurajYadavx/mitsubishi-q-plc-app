"""
tests/test_modbus_adapter.py
────────────────────────────
Comprehensive integration test suite for ModbusTcpAdapter.
Tests all four read function codes, data types, signedness, word order,
scaling, protocol exceptions, failure isolation, and read-only enforcement.
Uses the local MockModbusServer fixture with zero external dependencies.
"""

import sys
import unittest
from pathlib import Path

import ast

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.modbus.modbus_tcp import (
    ModbusAddress,
    ModbusTable,
    ModbusTcpAdapter,
    _decode_registers,
    parse_modbus_address,
)
from core.adapter_interface import IPlcProtocolAdapter, ReadOnlySecurityViolation
from core.adapter_registry import AdapterRegistry
from core.config_loader import DeviceConfig, GatewayConfig
from core.engine import GatewayEngine
from core.models import DataQuality, PointDataType, PointDefinition
from core.transport_interface import MockTelemetryTransport
from tests.mock_modbus_server import MockModbusServer


class TestModbusAddressParser(unittest.TestCase):
    """Test suite for Modbus address parsing conventions."""

    def test_prefix_parsing(self):
        # Coils
        self.assertEqual(parse_modbus_address("coil:0"), ModbusAddress(ModbusTable.COIL, 0, "coil:0"))
        self.assertEqual(parse_modbus_address("c:15"), ModbusAddress(ModbusTable.COIL, 15, "c:15"))

        # Discrete Inputs
        self.assertEqual(parse_modbus_address("di:5"), ModbusAddress(ModbusTable.DISCRETE_INPUT, 5, "di:5"))
        self.assertEqual(parse_modbus_address("disc:20"), ModbusAddress(ModbusTable.DISCRETE_INPUT, 20, "disc:20"))

        # Holding Registers
        self.assertEqual(parse_modbus_address("hr:100"), ModbusAddress(ModbusTable.HOLDING_REGISTER, 100, "hr:100"))
        self.assertEqual(parse_modbus_address("holding:0"), ModbusAddress(ModbusTable.HOLDING_REGISTER, 0, "holding:0"))

        # Input Registers
        self.assertEqual(parse_modbus_address("ir:50"), ModbusAddress(ModbusTable.INPUT_REGISTER, 50, "ir:50"))
        self.assertEqual(parse_modbus_address("input:1"), ModbusAddress(ModbusTable.INPUT_REGISTER, 1, "input:1"))

    def test_bit_within_register_parsing(self):
        addr = parse_modbus_address("hr:10.5")
        self.assertEqual(addr.table, ModbusTable.HOLDING_REGISTER)
        self.assertEqual(addr.offset, 10)
        self.assertEqual(addr.bit_index, 5)

    def test_modicon_5digit_and_6digit_notation(self):
        # Coils: 00001 -> offset 0
        self.assertEqual(parse_modbus_address("00001"), ModbusAddress(ModbusTable.COIL, 0, "00001"))
        self.assertEqual(parse_modbus_address("00010"), ModbusAddress(ModbusTable.COIL, 9, "00010"))

        # Discrete Inputs: 10001 -> offset 0
        self.assertEqual(parse_modbus_address("10001"), ModbusAddress(ModbusTable.DISCRETE_INPUT, 0, "10001"))
        self.assertEqual(parse_modbus_address("100001"), ModbusAddress(ModbusTable.DISCRETE_INPUT, 0, "100001"))

        # Input Registers: 30001 -> offset 0
        self.assertEqual(parse_modbus_address("30001"), ModbusAddress(ModbusTable.INPUT_REGISTER, 0, "30001"))
        self.assertEqual(parse_modbus_address("300001"), ModbusAddress(ModbusTable.INPUT_REGISTER, 0, "300001"))

        # Holding Registers: 40001 -> offset 0
        self.assertEqual(parse_modbus_address("40001"), ModbusAddress(ModbusTable.HOLDING_REGISTER, 0, "40001"))
        self.assertEqual(parse_modbus_address("400001"), ModbusAddress(ModbusTable.HOLDING_REGISTER, 0, "400001"))

    def test_modicon_boundaries_and_invalid_references(self):
        """Test boundary limits for 5-digit and 6-digit Modicon notations and reject invalid references."""
        # 5-digit boundaries
        self.assertEqual(parse_modbus_address("00001").offset, 0)
        self.assertEqual(parse_modbus_address("09999").offset, 9998)
        self.assertEqual(parse_modbus_address("10001").offset, 0)
        self.assertEqual(parse_modbus_address("19999").offset, 9998)
        self.assertEqual(parse_modbus_address("30001").offset, 0)
        self.assertEqual(parse_modbus_address("39999").offset, 9998)
        self.assertEqual(parse_modbus_address("40001").offset, 0)
        self.assertEqual(parse_modbus_address("49999").offset, 9998)

        # 6-digit boundaries
        self.assertEqual(parse_modbus_address("000001").offset, 0)
        self.assertEqual(parse_modbus_address("065536").offset, 65535)
        self.assertEqual(parse_modbus_address("100001").offset, 0)
        self.assertEqual(parse_modbus_address("165536").offset, 65535)
        self.assertEqual(parse_modbus_address("300001").offset, 0)
        self.assertEqual(parse_modbus_address("365536").offset, 65535)
        self.assertEqual(parse_modbus_address("400001").offset, 0)
        self.assertEqual(parse_modbus_address("465536").offset, 65535)

        # Out-of-bounds 5-digit & 6-digit references must raise ValueError
        with self.assertRaises(ValueError):
            parse_modbus_address("00000")  # 1-based offset calculation gives -1
        with self.assertRaises(ValueError):
            parse_modbus_address("000000")
        with self.assertRaises(ValueError):
            parse_modbus_address("065537")  # Exceeds max 65535
        with self.assertRaises(ValueError):
            parse_modbus_address("10000")
        with self.assertRaises(ValueError):
            parse_modbus_address("100000")
        with self.assertRaises(ValueError):
            parse_modbus_address("165537")
        with self.assertRaises(ValueError):
            parse_modbus_address("30000")
        with self.assertRaises(ValueError):
            parse_modbus_address("300000")
        with self.assertRaises(ValueError):
            parse_modbus_address("365537")
        with self.assertRaises(ValueError):
            parse_modbus_address("40000")
        with self.assertRaises(ValueError):
            parse_modbus_address("400000")
        with self.assertRaises(ValueError):
            parse_modbus_address("465537")

        # Invalid Modicon table prefixes (2xxxx, 5xxxx, 9xxxx)
        with self.assertRaises(ValueError):
            parse_modbus_address("20001")
        with self.assertRaises(ValueError):
            parse_modbus_address("50001")
        with self.assertRaises(ValueError):
            parse_modbus_address("99999")

    def test_bit_indexing_restrictions_and_bounds(self):
        """Verify bit indexing boundaries (0-15) and rejection on discrete 1-bit points."""
        # Valid bit boundaries (0 = LSB, 15 = MSB)
        addr_lsb = parse_modbus_address("hr:0.0")
        self.assertEqual(addr_lsb.bit_index, 0)
        addr_msb = parse_modbus_address("hr:0.15")
        self.assertEqual(addr_msb.bit_index, 15)

        # Modicon bit indexing
        self.assertEqual(parse_modbus_address("40001.0").bit_index, 0)
        self.assertEqual(parse_modbus_address("40001.15").bit_index, 15)
        self.assertEqual(parse_modbus_address("30001.7").bit_index, 7)

        # Out-of-bounds bit indexing
        with self.assertRaises(ValueError):
            parse_modbus_address("hr:0.16")
        with self.assertRaises(ValueError):
            parse_modbus_address("hr:0.-1")
        with self.assertRaises(ValueError):
            parse_modbus_address("hr:0.abc")

        # Bit indexing is prohibited on 1-bit discrete inputs and coils
        with self.assertRaises(ValueError):
            parse_modbus_address("coil:0.0")
        with self.assertRaises(ValueError):
            parse_modbus_address("00001.1")
        with self.assertRaises(ValueError):
            parse_modbus_address("di:0.5")
        with self.assertRaises(ValueError):
            parse_modbus_address("10001.2")

    def test_invalid_address_syntax_rejected(self):
        with self.assertRaises(ValueError):
            parse_modbus_address("")
        with self.assertRaises(ValueError):
            parse_modbus_address("xyz:100")
        with self.assertRaises(ValueError):
            parse_modbus_address("hr:-5")
        with self.assertRaises(ValueError):
            parse_modbus_address("hr:70000")  # Exceeds 65535


class TestRegisterDecodingAndEdgeCases(unittest.TestCase):
    """Unit tests for register decoding, incomplete reads, and endianness handling."""

    def test_decode_registers_incomplete_reads_for_32bit(self):
        """Incomplete reads (<2 registers) for 32-bit types must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            _decode_registers([1234], PointDataType.FLOAT32)
        self.assertIn("Incomplete read", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            _decode_registers([1234], PointDataType.UINT32)
        self.assertIn("Incomplete read", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            _decode_registers([1234], PointDataType.INT32)
        self.assertIn("Incomplete read", str(ctx.exception))

    def test_decode_registers_empty_list(self):
        """Empty register list must fail fast with ValueError."""
        with self.assertRaises(ValueError):
            _decode_registers([], PointDataType.UINT16)

    def test_decode_registers_unsupported_word_order(self):
        """Unrecognized word order string must raise ValueError."""
        with self.assertRaises(ValueError):
            _decode_registers([1, 2], PointDataType.UINT32, word_order="middle_endian")

    def test_boolean_decoding_normalization(self):
        """Register value to boolean decoding must strictly return 0 or 1."""
        self.assertEqual(_decode_registers([0], PointDataType.BOOLEAN), 0)
        self.assertEqual(_decode_registers([1], PointDataType.BOOLEAN), 1)
        self.assertEqual(_decode_registers([256], PointDataType.BOOLEAN), 1)


class TestModbusTcpAdapterIntegration(unittest.TestCase):
    """Integration test suite executing read operations against MockModbusServer."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockModbusServer()
        cls.port = cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        # Reset server state
        self.server.coils.clear()
        self.server.discrete_inputs.clear()
        self.server.holding_registers.clear()
        self.server.input_registers.clear()
        self.server.received_function_codes.clear()
        self.server.write_requests_received.clear()
        self.server.injected_exceptions.clear()
        self.server.inject_delay_sec = 0.0
        self.server.simulate_drop = False

        self.adapter = ModbusTcpAdapter(
            host="127.0.0.1",
            port=self.port,
            unit_id=1,
            timeout=1.0,
            word_order="big_endian",
        )

    def tearDown(self):
        self.adapter.disconnect()

    # -------------------------------------------------------------------------
    # Core Function Codes & Value Verification
    # -------------------------------------------------------------------------

    def test_read_all_four_function_codes(self):
        """
        Verify that all four required read operations succeed:
          FC 0x01: Read Coils
          FC 0x02: Read Discrete Inputs
          FC 0x03: Read Holding Registers
          FC 0x04: Read Input Registers
        """
        self.server.set_coil(0, True)
        self.server.set_discrete_input(5, True)
        self.server.set_holding_register(10, 1234)
        self.server.set_input_register(20, 5678)

        points = [
            PointDefinition(point_id="p_coil", device_address="coil:0", data_type=PointDataType.BOOLEAN),
            PointDefinition(point_id="p_di", device_address="di:5", data_type=PointDataType.BOOLEAN),
            PointDefinition(point_id="p_hr", device_address="hr:10", data_type=PointDataType.UINT16),
            PointDefinition(point_id="p_ir", device_address="ir:20", data_type=PointDataType.UINT16),
        ]

        self.assertTrue(self.adapter.connect())
        batch = self.adapter.read_points(points)

        self.assertTrue(batch.connected)
        self.assertEqual(len(batch.readings), 4)

        # Verify values and good quality
        self.assertEqual(batch.readings["p_coil"].raw_value, 1)
        self.assertEqual(batch.readings["p_coil"].quality, DataQuality.GOOD)

        self.assertEqual(batch.readings["p_di"].raw_value, 1)
        self.assertEqual(batch.readings["p_di"].quality, DataQuality.GOOD)

        self.assertEqual(batch.readings["p_hr"].raw_value, 1234)
        self.assertEqual(batch.readings["p_hr"].quality, DataQuality.GOOD)

        self.assertEqual(batch.readings["p_ir"].raw_value, 5678)
        self.assertEqual(batch.readings["p_ir"].quality, DataQuality.GOOD)

        # Check server received FC 0x01, 0x02, 0x03, 0x04
        self.assertIn(0x01, self.server.received_function_codes)
        self.assertIn(0x02, self.server.received_function_codes)
        self.assertIn(0x03, self.server.received_function_codes)
        self.assertIn(0x04, self.server.received_function_codes)

    def test_valid_zero_reading_vs_communication_failure(self):
        """
        CRITICAL INDUSTRIAL REQUIREMENT:
        Distinguish a legitimate 0 reading from a disconnected/failed state.
        """
        self.server.set_coil(0, False)
        self.server.set_holding_register(0, 0)

        points = [
            PointDefinition("c0", "coil:0", PointDataType.BOOLEAN),
            PointDefinition("hr0", "hr:0", PointDataType.UINT16),
        ]

        self.adapter.connect()
        batch_good_zero = self.adapter.read_points(points)

        # Legitimate zeros must have quality GOOD and raw_value 0
        self.assertTrue(batch_good_zero.connected)
        self.assertEqual(batch_good_zero.readings["c0"].raw_value, 0)
        self.assertEqual(batch_good_zero.readings["c0"].quality, DataQuality.GOOD)
        self.assertTrue(batch_good_zero.readings["c0"].is_valid)

        self.assertEqual(batch_good_zero.readings["hr0"].raw_value, 0)
        self.assertEqual(batch_good_zero.readings["hr0"].quality, DataQuality.GOOD)
        self.assertTrue(batch_good_zero.readings["hr0"].is_valid)

        # Now disconnect and verify failure reading produces raw_value=None, not 0!
        self.adapter.disconnect()
        batch_failed = self.adapter.read_points(points)

        self.assertFalse(batch_failed.connected)
        for pid, r in batch_failed.readings.items():
            self.assertIsNone(r.raw_value, f"Point {pid} must have null raw_value on failure, not 0!")
            self.assertEqual(r.quality, DataQuality.COMM_FAILURE)
            self.assertFalse(r.is_valid)

    # -------------------------------------------------------------------------
    # Signedness and 32-bit Types
    # -------------------------------------------------------------------------

    def test_signed_and_unsigned_16bit_registers(self):
        """Verify signed INT16 negative numbers (-1, -32768) and unsigned UINT16 (50000)."""
        # Register 0: 0xFFFF -> signed -1, unsigned 65535
        # Register 1: 0x8000 -> signed -32768, unsigned 32768
        self.server.set_holding_register(0, 0xFFFF)
        self.server.set_holding_register(1, 0x8000)

        points = [
            PointDefinition("u16_max", "hr:0", PointDataType.UINT16),
            PointDefinition("s16_minus_1", "hr:0", PointDataType.INT16),
            PointDefinition("s16_min", "hr:1", PointDataType.INT16),
        ]

        self.adapter.connect()
        batch = self.adapter.read_points(points)

        self.assertEqual(batch.readings["u16_max"].raw_value, 65535)
        self.assertEqual(batch.readings["s16_minus_1"].raw_value, -1)
        self.assertEqual(batch.readings["s16_min"].raw_value, -32768)

    def test_32bit_integers_and_word_order(self):
        """Verify 32-bit INT32/UINT32 across two registers with big_endian and word_swap."""
        val = 123456789

        # 1. Big Endian (default)
        self.server.set_holding_registers_32bit(0, val, is_float=False, word_order="big_endian")
        adapter_be = ModbusTcpAdapter("127.0.0.1", port=self.port, word_order="big_endian")
        adapter_be.connect()

        batch_be = adapter_be.read_points([PointDefinition("int32_be", "hr:0", PointDataType.INT32)])
        self.assertEqual(batch_be.readings["int32_be"].raw_value, val)
        adapter_be.disconnect()

        # 2. Little Endian / Word Swap
        self.server.set_holding_registers_32bit(10, val, is_float=False, word_order="word_swap")
        adapter_ws = ModbusTcpAdapter("127.0.0.1", port=self.port, word_order="word_swap")
        adapter_ws.connect()

        batch_ws = adapter_ws.read_points([PointDefinition("int32_ws", "hr:10", PointDataType.INT32)])
        self.assertEqual(batch_ws.readings["int32_ws"].raw_value, val)
        adapter_ws.disconnect()

    def test_32bit_float(self):
        """Verify 32-bit IEEE 754 single precision float."""
        float_val = 23.5
        self.server.set_holding_registers_32bit(0, float_val, is_float=True, word_order="big_endian")

        self.adapter.connect()
        batch = self.adapter.read_points([PointDefinition("temp_f32", "hr:0", PointDataType.FLOAT32)])
        self.assertEqual(batch.readings["temp_f32"].raw_value, 23.5)

    def test_engineering_scaling(self):
        """Verify scaling and offset application through common model."""
        self.server.set_holding_register(0, 200)

        # raw = 200 -> eng = (200 * 0.1) + 5.0 = 25.0
        point = PointDefinition(
            point_id="scaled_temp",
            device_address="hr:0",
            data_type=PointDataType.INT16,
            scale_factor=0.1,
            offset=5.0,
            engineering_units="°C",
        )

        self.adapter.connect()
        batch = self.adapter.read_points([point])
        reading = batch.readings["scaled_temp"]

        self.assertEqual(reading.raw_value, 200)
        self.assertEqual(reading.engineering_value, 25.0)
        self.assertEqual(reading.quality, DataQuality.GOOD)

    def test_bit_within_register_extraction(self):
        """Verify extraction of specific bit index from a holding register."""
        # Value 0b0000_1000 = 8 (bit 3 is 1, bit 2 is 0)
        self.server.set_holding_register(0, 8)

        p_bit3 = PointDefinition("b3", "hr:0.3", PointDataType.BOOLEAN)
        p_bit2 = PointDefinition("b2", "hr:0.2", PointDataType.BOOLEAN)

        self.adapter.connect()
        batch = self.adapter.read_points([p_bit3, p_bit2])

        self.assertEqual(batch.readings["b3"].raw_value, 1)
        self.assertEqual(batch.readings["b2"].raw_value, 0)

    # -------------------------------------------------------------------------
    # Protocol Exceptions and Timeouts
    # -------------------------------------------------------------------------

    def test_protocol_exception_handling(self):
        """Verify adapter handles Modbus exception response gracefully without crashing."""
        # Inject Modbus Exception 0x02 (Illegal Data Address) for FC 0x03
        self.server.injected_exceptions[0x03] = 0x02

        self.adapter.connect()
        batch = self.adapter.read_points([PointDefinition("bad_addr", "hr:9999", PointDataType.UINT16)])

        self.assertTrue(batch.connected)
        r = batch.readings["bad_addr"]
        self.assertEqual(r.quality, DataQuality.BAD)
        self.assertIsNone(r.raw_value)
        self.assertIn("Modbus FC03 Error", r.error_message)

    # -------------------------------------------------------------------------
    # Read-Only Security Enforcement
    # -------------------------------------------------------------------------

    def test_read_only_violation_traps(self):
        """Calling write methods on ModbusTcpAdapter must fail closed with ReadOnlySecurityViolation."""
        with self.assertRaises(ReadOnlySecurityViolation):
            self.adapter.write("hr:0", 123)

        with self.assertRaises(ReadOnlySecurityViolation):
            self.adapter.force_coil("coil:0", True)

        with self.assertRaises(ReadOnlySecurityViolation):
            self.adapter.write_register("hr:0", 123)

        with self.assertRaises(ReadOnlySecurityViolation):
            self.adapter.write_coils("coil:0", [True, False])

        with self.assertRaises(ReadOnlySecurityViolation):
            self.adapter.write_registers("hr:0", [10, 20])

    def test_adapter_reconnect_lifecycle(self):
        """Verify explicit disconnect followed by connect cleanly restores communication."""
        self.server.set_holding_register(5, 777)
        pt = PointDefinition("reg5", "hr:5", PointDataType.UINT16)

        # 1. Connect and read
        self.assertTrue(self.adapter.connect())
        self.assertTrue(self.adapter.is_connected)
        b1 = self.adapter.read_points([pt])
        self.assertTrue(b1.connected)
        self.assertEqual(b1.readings["reg5"].raw_value, 777)

        # 2. Disconnect
        self.adapter.disconnect()
        self.assertFalse(self.adapter.is_connected)
        b2 = self.adapter.read_points([pt])
        self.assertFalse(b2.connected)
        self.assertEqual(b2.readings["reg5"].quality, DataQuality.COMM_FAILURE)

        # 3. Reconnect
        self.assertTrue(self.adapter.connect())
        self.assertTrue(self.adapter.is_connected)
        b3 = self.adapter.read_points([pt])
        self.assertTrue(b3.connected)
        self.assertEqual(b3.readings["reg5"].raw_value, 777)

    def test_ast_static_inspection_no_write_calls_in_adapter(self):
        """Static AST audit: verify adapters/modbus/modbus_tcp.py issues NO Modbus write calls."""
        modbus_file = REPO_ROOT / "adapters" / "modbus" / "modbus_tcp.py"
        with open(modbus_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(modbus_file))

        prohibited_methods = {
            "write_coil",
            "write_coils",
            "write_register",
            "write_registers",
            "mask_write_register",
            "read_write_multiple_registers",
        }

        found_prohibited_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                attr_name = getattr(func, "attr", None) or getattr(func, "id", None)
                if attr_name in prohibited_methods:
                    found_prohibited_calls.append((attr_name, node.lineno))

        self.assertEqual(
            found_prohibited_calls,
            [],
            f"Found prohibited Modbus write method calls in adapter: {found_prohibited_calls}"
        )

    def test_server_receives_zero_write_requests(self):
        """Verify mock server confirms that zero write requests were received across all operations."""
        self.assertEqual(len(self.server.write_requests_received), 0)


class TestModbusGatewayIntegration(unittest.TestCase):
    """Integration test verifying GatewayEngine running multi-vendor devices simultaneously."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockModbusServer()
        cls.port = cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_simulated_and_modbus_devices_coexistence_and_failure_isolation(self):
        """
        Verify GatewayEngine polls BOTH a Simulated PLC and a Modbus TCP device.
        When Modbus fails, Simulated PLC continues polling normally with zero interruption.
        """
        self.server.set_holding_register(0, 450)
        self.server.set_coil(0, True)

        registry = AdapterRegistry()
        transport = MockTelemetryTransport()

        # Device 1: Simulated PLC
        dev_sim = DeviceConfig(
            id="sim_device_01",
            name="Virtual PLC",
            enabled=True,
            adapter_id="simulated",
            poll_interval_sec=0.05,
            points=[PointDefinition("X3", "X3", PointDataType.BOOLEAN)],
        )

        # Device 2: Modbus TCP Controller
        dev_modbus = DeviceConfig(
            id="modbus_device_01",
            name="Lab Modbus OCS",
            enabled=True,
            adapter_id="modbus_tcp",
            poll_interval_sec=0.05,
            connection={"host": "127.0.0.1", "port": self.port, "unit_id": 1},
            points=[
                PointDefinition("run_coil", "coil:0", PointDataType.BOOLEAN),
                PointDefinition("speed_hr", "hr:0", PointDataType.UINT16),
            ],
        )

        gateway_config = GatewayConfig(
            id="multi-gw-01",
            poll_interval_sec=0.05,
            devices=[dev_sim, dev_modbus],
        )

        engine = GatewayEngine(config=gateway_config, registry=registry, transport=transport)
        engine.start()

        # Step 1: Normal poll cycle -> both devices succeed
        batches = engine.poll_once()
        self.assertEqual(len(batches), 2)

        batch_sim = next(b for b in batches if b.device_id == "sim_device_01")
        batch_mb = next(b for b in batches if b.device_id == "modbus_device_01")

        self.assertTrue(batch_sim.connected)
        self.assertTrue(batch_mb.connected)
        self.assertEqual(batch_mb.readings["speed_hr"].raw_value, 450)

        # Step 2: Modbus server drops / disconnects
        self.server.simulate_drop = True

        import time
        time.sleep(0.06)
        batches_fail = engine.poll_once()

        batch_sim2 = next(b for b in batches_fail if b.device_id == "sim_device_01")
        batch_mb2 = next(b for b in batches_fail if b.device_id == "modbus_device_01")

        # Crucial verification: Simulated device continues running healthy!
        self.assertTrue(batch_sim2.connected, "Simulated device must remain connected")
        # Modbus device captured failure gracefully
        self.assertFalse(batch_mb2.connected, "Modbus device must report failure without crashing engine")

        engine.stop()


if __name__ == "__main__":
    unittest.main()
