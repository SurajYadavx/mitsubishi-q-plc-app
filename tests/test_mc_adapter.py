"""
tests/test_mc_adapter.py
────────────────────────
Unit tests for Mitsubishi MELSEC-Q MC Protocol Adapter.
Validates address conversion, metadata, error handling, and interface compliance.
"""

import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.mitsubishi.mc_protocol import (
    MitsubishiMcProtocolAdapter,
    _addr_to_label,
    _device_to_addr,
)
from core.adapter_interface import IPlcProtocolAdapter
from core.models import DataQuality, PointDataType, PointDefinition


class TestMitsubishiMcAdapter(unittest.TestCase):
    """Test suite for Mitsubishi MC Protocol 3E binary adapter."""

    def test_implements_interface(self):
        """Verify adapter inherits and implements IPlcProtocolAdapter."""
        adapter = MitsubishiMcProtocolAdapter(host="192.168.3.39", port=5010)
        self.assertIsInstance(adapter, IPlcProtocolAdapter)
        self.assertEqual(adapter.metadata.adapter_id, "mitsubishi_mc_3e")
        self.assertTrue(adapter.metadata.capabilities.is_strictly_read_only)
        self.assertFalse(adapter.is_connected)

    def test_q_series_hex_address_conversion(self):
        """
        Verify Mitsubishi Q-series hexadecimal device addressing:
        X0–X7 -> decimal 0–7
        Y20   -> hex 0x20 = decimal 32
        Y27   -> hex 0x27 = decimal 39
        B1A   -> hex 0x1A = decimal 26
        """
        self.assertEqual(_device_to_addr("X0"), ("X", 0))
        self.assertEqual(_device_to_addr("X7"), ("X", 7))
        self.assertEqual(_device_to_addr("Y20"), ("Y", 32))
        self.assertEqual(_device_to_addr("Y27"), ("Y", 39))
        self.assertEqual(_device_to_addr("B1A"), ("B", 26))

        # Reverse label mapping
        self.assertEqual(_addr_to_label("X", 0), "X0")
        self.assertEqual(_addr_to_label("X", 7), "X7")
        self.assertEqual(_addr_to_label("Y", 32), "Y20")
        self.assertEqual(_addr_to_label("Y", 39), "Y27")
        self.assertEqual(_addr_to_label("B", 26), "B1A")

    def test_decimal_devices_conversion(self):
        """Verify decimal devices (M, D, R) use base 10."""
        self.assertEqual(_device_to_addr("D100"), ("D", 100))
        self.assertEqual(_device_to_addr("M50"), ("M", 50))
        self.assertEqual(_addr_to_label("D", 100), "D100")
        self.assertEqual(_addr_to_label("M", 50), "M50")

    def test_invalid_address_raises_value_error(self):
        """Verify malformed address strings raise ValueError."""
        with self.assertRaises(ValueError):
            _device_to_addr("")
        with self.assertRaises(ValueError):
            _device_to_addr("X")
        with self.assertRaises(ValueError):
            _device_to_addr("XGG")  # Not valid hex

    def test_disconnected_poll_returns_comm_failure_not_zeros(self):
        """
        CRITICAL SAFETY TEST:
        When disconnected, the adapter MUST return DataQuality.COMM_FAILURE with error details,
        and MUST NOT fabricate false zero readings!
        """
        adapter = MitsubishiMcProtocolAdapter(host="192.168.3.39", port=5010)
        points = [
            PointDefinition(point_id="X0", device_address="X0", data_type=PointDataType.BOOLEAN),
            PointDefinition(point_id="Y20", device_address="Y20", data_type=PointDataType.BOOLEAN),
        ]

        batch = adapter.read_points(points)

        self.assertFalse(batch.connected)
        self.assertIsNotNone(batch.error)
        self.assertEqual(len(batch.readings), 2)

        for pt_id, reading in batch.readings.items():
            self.assertEqual(reading.quality, DataQuality.COMM_FAILURE)
            self.assertIsNone(reading.raw_value, f"Point {pt_id} must have null raw_value on failure, not 0!")
            self.assertFalse(reading.is_valid)
            self.assertIsNotNone(reading.error_message)

    def test_diagnostics_and_device_info(self):
        adapter = MitsubishiMcProtocolAdapter(host="192.168.3.39", port=5010)
        diag = adapter.get_diagnostics()
        self.assertEqual(diag["adapter_id"], "mitsubishi_mc_3e")
        self.assertEqual(diag["host"], "192.168.3.39")
        self.assertEqual(diag["port"], 5010)
        self.assertFalse(diag["connected"])

        info = adapter.get_device_info()
        self.assertEqual(info["vendor"], "Mitsubishi Electric")
        self.assertEqual(info["series"], "MELSEC-Q")


if __name__ == "__main__":
    unittest.main()
