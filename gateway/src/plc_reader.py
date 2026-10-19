"""
gateway/src/plc_reader.py
─────────────────────────
READ-ONLY interface to the Mitsubishi Q03UDECPU via MC Protocol (3E-frame/TCP).

REFACTOR NOTE (Phase 02):
This module delegates directly to the canonical Mitsubishi MC Protocol adapter
(adapters/mitsubishi/mc_protocol.py) to eliminate duplicate implementations
while preserving full backward compatibility for existing scripts and tests.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import canonical implementations from adapters/mitsubishi/mc_protocol.py
from adapters.mitsubishi.mc_protocol import (
    MitsubishiMcProtocolAdapter,
    _addr_to_label,
    _device_to_addr,
)
from core.models import PointDataType, PointDefinition

logger = logging.getLogger(__name__)


@dataclass
class DeviceReading:
    """One complete poll of all monitored I/O (Legacy compatibility dataclass)."""
    timestamp_iso: str = ""
    inputs: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, int] = field(default_factory=dict)
    connected: bool = False
    error: Optional[str] = None


class PLCReader:
    """
    Manages a persistent 3E-frame MC Protocol TCP connection to the Q03UDECPU.
    Delegates to MitsubishiMcProtocolAdapter to guarantee a single implementation.
    """

    def __init__(
        self,
        ip: str = "192.168.3.39",
        port: int = 5010,
        timeout: float = 3.0,
        input_devices: list[str] | None = None,
        output_devices: list[str] | None = None,
    ) -> None:
        self.ip = ip
        self.port = port
        self.timeout = timeout

        self.input_devices: list[str] = input_devices or [
            "X0", "X1", "X2", "X3", "X4", "X5", "X6", "X7"
        ]
        self.output_devices: list[str] = output_devices or [
            "Y20", "Y21", "Y22", "Y23", "Y24", "Y25", "Y26", "Y27"
        ]

        # Canonical adapter instance
        self._adapter = MitsubishiMcProtocolAdapter(
            host=self.ip,
            port=self.port,
            timeout=self.timeout,
            frame_type="3E",
        )

        # Build PointDefinition list
        self._points: list[PointDefinition] = []
        for dev in self.input_devices:
            self._points.append(
                PointDefinition(point_id=dev, device_address=dev, data_type=PointDataType.BOOLEAN)
            )
        for dev in self.output_devices:
            self._points.append(
                PointDefinition(point_id=dev, device_address=dev, data_type=PointDataType.BOOLEAN)
            )

    @property
    def connected(self) -> bool:
        return self._adapter.is_connected

    def connect(self) -> bool:
        """Establish connection via canonical adapter."""
        return self._adapter.connect()

    def disconnect(self) -> None:
        """Disconnect via canonical adapter."""
        self._adapter.disconnect()

    def poll(self) -> DeviceReading:
        """
        Poll all configured input and output device bits from the PLC.
        Delegates to adapter.read_points() and converts to legacy DeviceReading.
        """
        batch = self._adapter.read_points(self._points)
        legacy = batch.to_legacy_payload()

        return DeviceReading(
            timestamp_iso=batch.timestamp_utc,
            inputs=legacy.get("inputs", {}),
            outputs=legacy.get("outputs", {}),
            connected=batch.connected,
            error=batch.error,
        )

    def _read_bit_devices(self, device_list: list[str]) -> dict[str, int]:
        """Internal helper maintained for backward compatibility."""
        pts = [
            PointDefinition(point_id=d, device_address=d, data_type=PointDataType.BOOLEAN)
            for d in device_list
        ]
        batch = self._adapter.read_points(pts)
        result: dict[str, int] = {}
        for d in device_list:
            if d in batch.readings:
                val = batch.readings[d].raw_value
                result[d] = int(bool(val)) if val is not None else 0
        return result


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.DEBUG)
    reader = PLCReader(ip="192.168.3.39", port=5010)
    print("Testing PLCReader delegation...")
    print(f"Adapter: {reader._adapter.metadata.display_name}")
