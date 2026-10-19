"""
tests/test_gateway_core.py
──────────────────────────
Unit tests for multi-device GatewayEngine, independent failure handling,
transport dispatching, and shutdown lifecycle.
"""

import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapter_registry import AdapterRegistry
from core.config_loader import DeviceConfig, GatewayConfig
from core.engine import GatewayEngine
from core.models import PointDataType, PointDefinition
from core.simulated_adapter import SimulatedPlcAdapter
from core.transport_interface import MockTelemetryTransport


class TestGatewayCoreEngine(unittest.TestCase):
    """Test suite for the GatewayEngine multi-device orchestrator."""

    def setUp(self):
        # Create an isolated registry with custom simulated adapters
        self.registry = AdapterRegistry()
        self.transport = MockTelemetryTransport(gateway_id="test-gw-01")

        # Define two devices: Device A (Healthy) and Device B (Failing)
        p1 = PointDefinition("X3", "X3", PointDataType.BOOLEAN)
        p2 = PointDefinition("Y20", "Y20", PointDataType.BOOLEAN)

        self.dev_a = DeviceConfig(
            id="device_healthy",
            name="Healthy Simulated PLC",
            enabled=True,
            adapter_id="simulated",
            poll_interval_sec=0.1,
            points=[p1, p2],
            connection={"simulate_fault": False},
        )

        self.dev_b = DeviceConfig(
            id="device_faulty",
            name="Faulty Simulated PLC",
            enabled=True,
            adapter_id="simulated",
            poll_interval_sec=0.1,
            points=[p1, p2],
            connection={"simulate_fault": True},
        )

        self.gateway_config = GatewayConfig(
            id="test-gw-01",
            name="Test Gateway",
            heartbeat_sec=1.0,
            poll_interval_sec=0.05,
            devices=[self.dev_a, self.dev_b],
        )

        self.engine = GatewayEngine(
            config=self.gateway_config,
            registry=self.registry,
            transport=self.transport,
        )

    def test_engine_initializes_enabled_devices(self):
        """Verify engine instantiates adapters for all enabled devices."""
        self.assertEqual(len(self.engine.active_devices), 2)
        self.assertIn("device_healthy", self.engine.active_devices)
        self.assertIn("device_faulty", self.engine.active_devices)

    def test_independent_device_failure_isolation(self):
        """
        CRITICAL MULTI-VENDOR ISOLATION TEST:
        When Device B fails or disconnects, Device A must continue polling successfully!
        """
        self.engine.start()

        # Execute one poll cycle
        batches = self.engine.poll_once()
        self.assertEqual(len(batches), 2)

        batch_a = next(b for b in batches if b.device_id == "device_healthy")
        batch_b = next(b for b in batches if b.device_id == "device_faulty")

        # Device A succeeded
        self.assertTrue(batch_a.connected, "Device A must be connected and healthy")
        self.assertIsNone(batch_a.error)
        self.assertEqual(batch_a.readings["X3"].raw_value, 1)  # Tick 1 simulation
        self.assertEqual(batch_a.readings["Y20"].raw_value, 1)

        # Device B failed, but gracefully captured
        self.assertFalse(batch_b.connected, "Device B must reflect failure")
        self.assertIsNotNone(batch_b.error)
        self.assertEqual(batch_b.readings["X3"].raw_value, None)

        # Verify transport received both payloads
        self.assertEqual(len(self.transport.sent_raw_payloads), 2)
        legacy_a = self.transport.sent_raw_payloads[0]
        legacy_b = self.transport.sent_raw_payloads[1]

        self.assertEqual(legacy_a["type"], "plc_reading")
        self.assertEqual(legacy_a["connected"], True)
        self.assertEqual(legacy_b["type"], "gateway_error")
        self.assertEqual(legacy_b["connected"], False)

        self.engine.stop()

    def test_monotonic_sequence_numbering(self):
        """Verify sequence numbers increment across multiple polls."""
        import time
        self.engine.start()
        self.engine.poll_once()
        time.sleep(0.12)
        self.engine.poll_once()

        self.assertGreaterEqual(len(self.transport.sent_raw_payloads), 4)
        seqs = [p["seq"] for p in self.transport.sent_raw_payloads]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)), "Sequence numbers must be unique and monotonic")

        self.engine.stop()

    def test_shutdown_lifecycle(self):
        """Verify graceful shutdown closes transports and emits shutdown heartbeat."""
        self.engine.start()
        self.assertTrue(self.transport.is_open)

        self.engine.stop()
        self.assertFalse(self.transport.is_open)

        # Check last heartbeat is shutdown signal
        self.assertGreater(len(self.transport.heartbeats), 0)
        last_hb = self.transport.heartbeats[-1]
        self.assertFalse(last_hb["connected"])
        self.assertIn("Shutdown", last_hb["error"])


if __name__ == "__main__":
    unittest.main()
