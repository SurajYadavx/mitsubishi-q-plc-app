"""
tests/test_read_only_enforcement.py
───────────────────────────────────
Automated Negative Security Tests: Enforcing strictly read-only operation.
Proves that protocol adapters and the gateway core do NOT expose or invoke PLC writes/forces.
"""

import inspect
import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.mitsubishi.mc_protocol import MitsubishiMcProtocolAdapter
from core.adapter_interface import IPlcProtocolAdapter, ReadOnlySecurityViolation
from core.simulated_adapter import SimulatedPlcAdapter


class TestReadOnlyEnforcement(unittest.TestCase):
    """
    Negative tests proving that write and force operations are structurally rejected
    and completely absent from the platform core.
    """

    def setUp(self):
        self.mc_adapter = MitsubishiMcProtocolAdapter(host="192.168.3.39", port=5010)
        self.sim_adapter = SimulatedPlcAdapter()

    def test_mc_adapter_write_raises_security_violation(self):
        """Calling write() on MitsubishiMcProtocolAdapter must raise ReadOnlySecurityViolation."""
        with self.assertRaises(ReadOnlySecurityViolation):
            self.mc_adapter.write("Y20", 1)

    def test_mc_adapter_force_coil_raises_security_violation(self):
        """Calling force_coil() on MitsubishiMcProtocolAdapter must raise ReadOnlySecurityViolation."""
        with self.assertRaises(ReadOnlySecurityViolation):
            self.mc_adapter.force_coil("Y20", True)

    def test_mc_adapter_write_register_raises_security_violation(self):
        """Calling write_register() on MitsubishiMcProtocolAdapter must raise ReadOnlySecurityViolation."""
        with self.assertRaises(ReadOnlySecurityViolation):
            self.mc_adapter.write_register("D100", 1234)

    def test_sim_adapter_write_raises_security_violation(self):
        """Calling write() on SimulatedPlcAdapter must raise ReadOnlySecurityViolation."""
        with self.assertRaises(ReadOnlySecurityViolation):
            self.sim_adapter.write("Y20", 1)

    def test_sim_adapter_force_coil_raises_security_violation(self):
        """Calling force_coil() on SimulatedPlcAdapter must raise ReadOnlySecurityViolation."""
        with self.assertRaises(ReadOnlySecurityViolation):
            self.sim_adapter.force_coil("Y20", True)

    def test_ast_inspection_no_write_opcodes_in_mc_adapter(self):
        """
        Verify via static inspection of mc_protocol.py source that NO Mitsubishi write opcodes
        (0x1401 batch write, 0x1402 random write, 0x1601 remote STOP/RUN) appear in the file.
        """
        mc_file = REPO_ROOT / "adapters" / "mitsubishi" / "mc_protocol.py"
        self.assertTrue(mc_file.exists())
        source = mc_file.read_text(encoding="utf-8")

        # Prohibited MC Protocol write opcodes
        prohibited_opcodes = ["0x1401", "0x1402", "0x1601", "0x1602", "batchwrite", "randomwrite"]
        for op in prohibited_opcodes:
            self.assertNotIn(
                op,
                source.lower(),
                f"Prohibited write opcode or method '{op}' found in {mc_file}!"
            )

    def test_capability_flags_affirm_strictly_read_only(self):
        """Verify that capability flags explicitly declare read-only invariants."""
        self.assertTrue(self.mc_adapter.metadata.capabilities.is_strictly_read_only)
        self.assertTrue(self.sim_adapter.metadata.capabilities.is_strictly_read_only)


if __name__ == "__main__":
    unittest.main()
