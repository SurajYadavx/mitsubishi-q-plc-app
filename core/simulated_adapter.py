"""
core/simulated_adapter.py
─────────────────────────
Deterministic Simulated PLC Adapter for offline execution and automated test suites.
Implements IPlcProtocolAdapter without requiring physical PLC hardware.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.adapter_interface import (
    AdapterCapabilities,
    IPlcProtocolAdapter,
    ProtocolMetadata,
)
from core.models import (
    DataQuality,
    NormalisedReading,
    NormalisedTelemetryBatch,
    PointDefinition,
    utc_now_iso,
)


class SimulatedPlcAdapter(IPlcProtocolAdapter):
    """
    Deterministic simulated controller adapter for unit testing and offline demo.
    Generates realistic bit and register values without hardware.
    """

    def __init__(
        self,
        name: str = "Simulated PLC",
        simulate_fault: bool = False,
        fixed_values: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._name = name
        self._simulate_fault = simulate_fault
        self._fixed_values: Dict[str, Any] = fixed_values or {}
        self._connected: bool = False
        self._tick_count: int = 0
        self._last_error: Optional[str] = None

        self._metadata = ProtocolMetadata(
            adapter_id="simulated",
            display_name=f"Deterministic Simulation Adapter ({name})",
            version="1.0.0",
            manufacturer="Virtual / Test Fixture",
            supported_models=["Simulated-Q03", "Generic-Simulated"],
            capabilities=AdapterCapabilities(
                can_read_bits=True,
                can_read_words=True,
                can_read_floats=True,
                can_read_strings=True,
                supports_batch_optimization=True,
                supports_diagnostics=True,
                supports_device_identification=True,
                is_strictly_read_only=True,
            ),
        )

    @property
    def metadata(self) -> ProtocolMetadata:
        return self._metadata

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_simulate_fault(self, fault: bool, error_msg: Optional[str] = None) -> None:
        """Inject or clear a simulated communication fault for testing."""
        self._simulate_fault = fault
        self._last_error = error_msg if fault else None

    def set_fixed_value(self, point_id: str, value: Any) -> None:
        """Set a deterministic value for a specific point."""
        self._fixed_values[point_id] = value

    def connect(self) -> bool:
        if self._simulate_fault:
            self._connected = False
            self._last_error = "Simulated connection refused"
            return False
        self._connected = True
        self._last_error = None
        return True

    def disconnect(self) -> None:
        self._connected = False

    def test_connection(self) -> Dict[str, Any]:
        return {
            "connected": self._connected and not self._simulate_fault,
            "latency_ms": 1.2,
            "error": self._last_error if self._simulate_fault else None,
            "details": {"mode": "simulated", "ticks": self._tick_count},
        }

    def read_points(
        self,
        points: List[PointDefinition],
        gateway_id: str = "plc-gw-01",
        device_id: str = "sim_device",
        sequence_number: int = 0,
    ) -> NormalisedTelemetryBatch:
        start_t = time.monotonic()
        ts = utc_now_iso()

        if not self._connected or self._simulate_fault:
            err = self._last_error or "Simulated adapter disconnected"
            readings = {
                pt.point_id: NormalisedReading.create_failure(
                    point_id=pt.point_id,
                    error_msg=err,
                    quality=DataQuality.COMM_FAILURE,
                    timestamp_utc=ts,
                )
                for pt in points
            }
            return NormalisedTelemetryBatch(
                gateway_id=gateway_id,
                device_id=device_id,
                sequence_number=sequence_number,
                timestamp_utc=ts,
                readings=readings,
                connected=False,
                scan_duration_ms=(time.monotonic() - start_t) * 1000.0,
                error=err,
            )

        self._tick_count += 1
        readings = {}

        # Synthesize ladder logic simulation: X3 pulse cycles every 2 ticks; drives Y20
        sim_x3 = 1 if (self._tick_count % 2 == 1) else 0

        for pt in points:
            pid = pt.point_id
            addr = pt.device_address.upper()

            # Check fixed value overrides first
            if pid in self._fixed_values:
                raw_val = self._fixed_values[pid]
            elif addr in self._fixed_values:
                raw_val = self._fixed_values[addr]
            elif addr == "X3":
                raw_val = sim_x3
            elif addr == "Y20":
                raw_val = sim_x3  # Rung: X3 drives Y20
            elif addr.startswith("X") or addr.startswith("Y"):
                raw_val = 0
            elif addr.startswith("D"):
                # Word register simulation: monotonic increment
                raw_val = (self._tick_count * 10) % 1000
            else:
                raw_val = 0

            readings[pid] = NormalisedReading.create_good(
                point=pt,
                raw_val=raw_val,
                timestamp_utc=ts,
            )

        duration_ms = (time.monotonic() - start_t) * 1000.0
        return NormalisedTelemetryBatch(
            gateway_id=gateway_id,
            device_id=device_id,
            sequence_number=sequence_number,
            timestamp_utc=ts,
            readings=readings,
            connected=True,
            scan_duration_ms=duration_ms,
            error=None,
        )

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "adapter_id": self._metadata.adapter_id,
            "connected": self._connected,
            "tick_count": self._tick_count,
            "last_error": self._last_error,
        }

    def get_device_info(self) -> Dict[str, str]:
        return {
            "vendor": "Simulated Industrial",
            "model": "SIM-Q03-VIRTUAL",
            "firmware": "2.0-SIM",
            "serial_number": "SIM-12345678",
        }
