"""
tests/test_core_models.py
─────────────────────────
Unit tests for core normalized telemetry models, data quality, and serialization.
"""

import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.models import (
    DataQuality,
    NormalisedReading,
    NormalisedTelemetryBatch,
    PointDataType,
    PointDefinition,
    utc_now_iso,
)


class TestPointDefinition(unittest.TestCase):
    """Test suite for PointDefinition creation, validation, and serialization."""

    def test_valid_point_creation(self):
        p = PointDefinition(
            point_id="temp_01",
            device_address="D100",
            data_type=PointDataType.INT16,
            engineering_units="°C",
            scale_factor=0.1,
            offset=5.0,
            description="Chamber temperature",
        )
        self.assertEqual(p.point_id, "temp_01")
        self.assertEqual(p.device_address, "D100")
        self.assertEqual(p.data_type, PointDataType.INT16)
        self.assertEqual(p.engineering_units, "°C")
        self.assertEqual(p.scale_factor, 0.1)
        self.assertEqual(p.offset, 5.0)

    def test_invalid_point_rejection(self):
        with self.assertRaises(ValueError):
            PointDefinition(point_id="", device_address="X0")

        with self.assertRaises(ValueError):
            PointDefinition(point_id="valid_id", device_address="")

        with self.assertRaises(ValueError):
            PointDefinition(point_id="p1", device_address="X0", data_type="INVALID_TYPE")  # type: ignore

    def test_serialization_roundtrip(self):
        p1 = PointDefinition(
            point_id="rpm_sensor",
            device_address="D200",
            data_type=PointDataType.UINT16,
            scale_factor=1.5,
            engineering_units="RPM",
        )
        d = p1.to_dict()
        p2 = PointDefinition.from_dict(d)
        self.assertEqual(p1, p2)


class TestNormalisedReading(unittest.TestCase):
    """Test suite for NormalisedReading, data quality states, and valid zero handling."""

    def test_valid_zero_reading(self):
        """CRITICAL: raw_value=0 or False must be treated as a VALID reading, not an error!"""
        point = PointDefinition(point_id="x3", device_address="X3", data_type=PointDataType.BOOLEAN)

        # Boolean 0
        r_zero = NormalisedReading.create_good(point, raw_val=0)
        self.assertEqual(r_zero.raw_value, 0)
        self.assertEqual(r_zero.engineering_value, 0)
        self.assertEqual(r_zero.quality, DataQuality.GOOD)
        self.assertTrue(r_zero.is_valid)
        self.assertIsNone(r_zero.error_message)

        # Boolean False
        r_false = NormalisedReading.create_good(point, raw_val=False)
        self.assertEqual(r_false.raw_value, False)
        self.assertEqual(r_false.engineering_value, 0)
        self.assertEqual(r_false.quality, DataQuality.GOOD)
        self.assertTrue(r_false.is_valid)

        # Integer 0
        p_int = PointDefinition(point_id="counter", device_address="D10", data_type=PointDataType.INT16)
        r_int_zero = NormalisedReading.create_good(p_int, raw_val=0)
        self.assertEqual(r_int_zero.raw_value, 0)
        self.assertEqual(r_int_zero.engineering_value, 0)
        self.assertEqual(r_int_zero.quality, DataQuality.GOOD)
        self.assertTrue(r_int_zero.is_valid)

    def test_failure_reading_has_null_and_error(self):
        """Failure must have raw_value=None and explicit COMM_FAILURE / BAD quality."""
        r_fail = NormalisedReading.create_failure(
            point_id="x3",
            error_msg="TCP connection timed out",
            quality=DataQuality.COMM_FAILURE,
        )
        self.assertIsNone(r_fail.raw_value)
        self.assertIsNone(r_fail.engineering_value)
        self.assertEqual(r_fail.quality, DataQuality.COMM_FAILURE)
        self.assertFalse(r_fail.is_valid)
        self.assertEqual(r_fail.error_message, "TCP connection timed out")

    def test_engineering_scaling(self):
        point = PointDefinition(
            point_id="pressure",
            device_address="D100",
            data_type=PointDataType.INT16,
            scale_factor=0.25,
            offset=-10.0,
        )
        # raw = 100 -> eng = (100 * 0.25) - 10.0 = 15.0
        reading = NormalisedReading.create_good(point, raw_val=100)
        self.assertEqual(reading.raw_value, 100)
        self.assertEqual(reading.engineering_value, 15.0)
        self.assertEqual(reading.quality, DataQuality.GOOD)

    def test_timestamp_utc_timezone_awareness(self):
        ts = utc_now_iso()
        self.assertTrue(ts.endswith("Z"), f"Expected UTC ISO string ending in Z, got: {ts}")
        self.assertIn("T", ts)


class TestNormalisedTelemetryBatch(unittest.TestCase):
    """Test suite for batch telemetry and legacy serialization compatibility."""

    def test_batch_serialization_roundtrip(self):
        p1 = PointDefinition(point_id="X0", device_address="X0")
        p2 = PointDefinition(point_id="Y20", device_address="Y20")

        r1 = NormalisedReading.create_good(p1, raw_val=1)
        r2 = NormalisedReading.create_good(p2, raw_val=0)

        batch = NormalisedTelemetryBatch(
            gateway_id="plc-gw-01",
            device_id="melsec_q03",
            sequence_number=101,
            timestamp_utc="2026-10-19T12:00:00.000Z",
            readings={"X0": r1, "Y20": r2},
            connected=True,
            scan_duration_ms=4.5,
        )

        d = batch.to_dict()
        self.assertEqual(d["type"], "universal_telemetry")
        self.assertEqual(d["gw_id"], "plc-gw-01")
        self.assertEqual(d["seq"], 101)
        self.assertEqual(d["connected"], True)
        self.assertIn("X0", d["readings"])
        self.assertIn("Y20", d["readings"])

        restored = NormalisedTelemetryBatch.from_dict(d)
        self.assertEqual(restored.gateway_id, batch.gateway_id)
        self.assertEqual(restored.sequence_number, batch.sequence_number)
        self.assertEqual(restored.readings["X0"].raw_value, 1)
        self.assertEqual(restored.readings["Y20"].raw_value, 0)

    def test_to_legacy_payload_compatibility(self):
        """
        Verify exact backward compatibility with the schema expected by:
        1. ESP8266 NodeMCU firmware
        2. Web Supervisory Dashboard (app.js)
        """
        readings = {
            "X0": NormalisedReading.create_good(PointDefinition("X0", "X0"), 0),
            "X3": NormalisedReading.create_good(PointDefinition("X3", "X3"), 1),
            "Y20": NormalisedReading.create_good(PointDefinition("Y20", "Y20"), 1),
            "Y21": NormalisedReading.create_good(PointDefinition("Y21", "Y21"), 0),
        }

        batch = NormalisedTelemetryBatch(
            gateway_id="plc-gw-01",
            device_id="melsec_q03",
            sequence_number=42,
            timestamp_utc="2026-10-19T12:00:00.000Z",
            readings=readings,
            connected=True,
        )

        legacy = batch.to_legacy_payload()

        # Check mandatory legacy fields
        self.assertEqual(legacy["type"], "plc_reading")
        self.assertEqual(legacy["gw_id"], "plc-gw-01")
        self.assertEqual(legacy["seq"], 42)
        self.assertEqual(legacy["ts"], "2026-10-19T12:00:00.000Z")
        self.assertEqual(legacy["connected"], True)
        self.assertIsNone(legacy["error"])

        # Check inputs and outputs maps
        self.assertIn("inputs", legacy)
        self.assertIn("outputs", legacy)
        self.assertEqual(legacy["inputs"]["X0"], 0)
        self.assertEqual(legacy["inputs"]["X3"], 1)
        self.assertEqual(legacy["outputs"]["Y20"], 1)
        self.assertEqual(legacy["outputs"]["Y21"], 0)


if __name__ == "__main__":
    unittest.main()
