"""
tests/test_config_loader.py
───────────────────────────
Unit tests for configuration loading, schema validation, and legacy INI migration fallback.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config_loader import (
    ConfigValidationError,
    load_legacy_ini_config,
    load_system_config,
    load_yaml_config,
)


class TestConfigLoader(unittest.TestCase):
    """Test suite for config loading and strict validation rules."""

    def test_load_default_devices_yaml(self):
        """Verify the repository's default config/devices.yaml loads and validates."""
        yaml_path = REPO_ROOT / "config" / "devices.yaml"
        self.assertTrue(yaml_path.exists(), "config/devices.yaml must exist")

        cfg = load_yaml_config(yaml_path)
        self.assertEqual(cfg.id, "plc-gw-01")
        self.assertGreater(len(cfg.devices), 0)

        # Check primary Q-series device
        q_dev = next((d for d in cfg.devices if d.id == "melsec_q03"), None)
        self.assertIsNotNone(q_dev, "melsec_q03 device must be defined in devices.yaml")
        self.assertTrue(q_dev.enabled)
        self.assertEqual(q_dev.adapter_id, "mitsubishi_mc_3e")
        self.assertEqual(q_dev.connection["host"], "192.168.3.39")
        self.assertEqual(q_dev.connection["port"], 5010)

        # Verify 8 inputs and 8 outputs are configured
        inputs = [p for p in q_dev.points if p.device_address.startswith("X")]
        outputs = [p for p in q_dev.points if p.device_address.startswith("Y")]
        self.assertEqual(len(inputs), 8)
        self.assertEqual(len(outputs), 8)

    def test_duplicate_device_id_rejection(self):
        """Ensure duplicate device IDs cause immediate validation failure."""
        bad_yaml = """
gateway:
  id: "test-gw"
devices:
  - id: "dev_01"
    name: "First Device"
    adapter_id: "simulated"
    points: []
  - id: "dev_01"
    name: "Duplicate Device"
    adapter_id: "simulated"
    points: []
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad_yaml)
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                load_yaml_config(tmp_path)
            self.assertIn("Duplicate device ID", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_duplicate_point_id_rejection(self):
        """Ensure duplicate point IDs within a device are rejected."""
        bad_yaml = """
gateway:
  id: "test-gw"
devices:
  - id: "dev_01"
    name: "Device"
    adapter_id: "simulated"
    points:
      - point_id: "p1"
        device_address: "X0"
      - point_id: "p1"
        device_address: "X1"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad_yaml)
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                load_yaml_config(tmp_path)
            self.assertIn("duplicate point_id", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_unknown_adapter_rejection(self):
        """Ensure unknown adapter identifiers are rejected explicitly."""
        bad_yaml = """
gateway:
  id: "test-gw"
devices:
  - id: "dev_01"
    name: "Device"
    adapter_id: "non_existent_protocol_xyz"
    points: []
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad_yaml)
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                load_yaml_config(tmp_path)
            self.assertIn("unknown adapter", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_malformed_address_rejection(self):
        """Ensure malformed device addresses (e.g. invalid prefix, invalid hex) fail validation."""
        bad_yaml = """
gateway:
  id: "test-gw"
devices:
  - id: "dev_01"
    name: "Device"
    adapter_id: "mitsubishi_mc_3e"
    points:
      - point_id: "bad_point"
        device_address: "Z99"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(bad_yaml)
            tmp_path = Path(f.name)

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                load_yaml_config(tmp_path)
            self.assertIn("Unsupported Mitsubishi device type prefix", str(ctx.exception))
        finally:
            tmp_path.unlink()

    def test_legacy_ini_migration_fallback(self):
        """Ensure legacy settings.ini format is loaded and converted to GatewayConfig flawlessly."""
        ini_content = """[plc]
ip = 192.168.3.39
port = 5010
protocol = 3E
timeout_sec = 3.0
poll_interval_sec = 1.0

[devices]
inputs = X0,X1,X2,X3,X4,X5,X6,X7
outputs = Y20,Y21,Y22,Y23,Y24,Y25,Y26,Y27

[serial]
port = COM9
baud_rate = 115200
timeout_sec = 1.0
ack_timeout_sec = 5.0

[gateway]
id = plc-gw-01
heartbeat_sec = 5
"""
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write(ini_content)
            tmp_path = Path(f.name)

        try:
            cfg = load_legacy_ini_config(tmp_path)
            self.assertEqual(cfg.id, "plc-gw-01")
            self.assertEqual(cfg.serial_port, "COM9")
            self.assertEqual(cfg.serial_baud, 115200)
            self.assertEqual(cfg.serial_ack_timeout, 5.0)

            # Single device created with correct settings
            self.assertEqual(len(cfg.devices), 1)
            dev = cfg.devices[0]
            self.assertEqual(dev.id, "melsec_q03")
            self.assertEqual(dev.connection["host"], "192.168.3.39")
            self.assertEqual(dev.connection["port"], 5010)
            self.assertEqual(len(dev.points), 16)  # 8 inputs + 8 outputs
        finally:
            tmp_path.unlink()

    def test_repo_default_settings_ini_exists_and_loads(self):
        """Verify repository settings.ini exists and successfully parses."""
        ini_path = REPO_ROOT / "gateway" / "config" / "settings.ini"
        self.assertTrue(ini_path.exists(), "gateway/config/settings.ini must exist")
        cfg = load_legacy_ini_config(ini_path)
        self.assertEqual(cfg.id, "plc-gw-01")
        self.assertTrue(isinstance(cfg.serial_port, str) and len(cfg.serial_port) > 0)
        self.assertEqual(cfg.serial_baud, 115200)
        self.assertEqual(cfg.serial_ack_timeout, 5.0)

    def test_load_system_config_auto_detection(self):
        """Test auto-detection priority in load_system_config."""
        cfg = load_system_config()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.id, "plc-gw-01")
        self.assertTrue(cfg.source_file.endswith("devices.yaml") or cfg.source_file.endswith("settings.ini"))


if __name__ == "__main__":
    unittest.main()
